"""Public facade, host ownership, and transient state-machine tests."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonl_viewer
from jsonl_viewer import ViewerSpec, view_jsonl

from tests.support import FakeHost, without_caret


def _source() -> bytes:
    records = (
        {
            "timestamp": "2026-08-29T09:00:00Z",
            "request_type": "request",
            "content": "alpha question",
            "latency_ms": 12,
            "ok": True,
        },
        {
            "timestamp": "2026-08-29T09:00:01Z",
            "request_type": "response",
            "content": "alpha answer",
            "latency_ms": 23,
            "ok": False,
        },
        {
            "timestamp": "2026-08-29T09:00:02Z",
            "request_type": "response",
            "content": "omega answer",
            "latency_ms": 34,
            "ok": None,
        },
    )
    return (
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        )
    ).encode("utf-8")


class PublicApiTests(unittest.TestCase):
    def test_facade_is_exact_and_spec_is_normalized_immutable(self) -> None:
        self.assertEqual(
            jsonl_viewer.__all__,
            ["ViewerHost", "ViewerSpec", "view_jsonl"],
        )
        spec = ViewerSpec(
            "session",
            "conversation",
            "agent",
        )
        self.assertFalse(hasattr(spec, "searchable_fields"))
        self.assertEqual(
            spec.primary_fields,
            ("timestamp", "request_type", "content"),
        )
        self.assertEqual(spec.conversation_label, "Conversation")
        self.assertIsNone(spec.conversation_subject)

        legacy = ViewerSpec(
            "s",
            "c",
            "a",
            "when",
            "kind",
            "payload",
            "Legacy title",
        )
        self.assertEqual(legacy.title, "Legacy title")
        self.assertEqual(legacy.conversation_label, "Conversation")
        self.assertIsNone(legacy.conversation_subject)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.agent_id = "changed"  # type: ignore[misc]

    def test_spec_rejects_removed_search_fields_and_bad_display_fields(self) -> None:
        with self.assertRaises(TypeError):
            ViewerSpec("s", "c", "a", searchable_fields=("content",))
        with self.assertRaisesRegex(ValueError, "control separator"):
            ViewerSpec("s", "c", "a", content_field="bad\tfield")
        with self.assertRaisesRegex(ValueError, "distinct"):
            ViewerSpec("s", "c", "a", date_time_field="same", request_type_field="same")

    def test_spec_validates_distinct_scope_header_parts(self) -> None:
        spec = ViewerSpec(
            "session",
            "d20260829T090000_abcd1234",
            "agent",
            conversation_label="Debate",
            conversation_subject="Provider response diagnostics",
        )
        self.assertEqual(spec.conversation_label, "Debate")
        self.assertEqual(
            spec.conversation_subject,
            "Provider response diagnostics",
        )
        for values, message in (
            ({"conversation_label": ""}, "conversation_label"),
            ({"conversation_label": 7}, "conversation_label"),
            ({"conversation_label": "x" * 513}, "too long"),
            ({"conversation_subject": ""}, "conversation_subject"),
            ({"conversation_subject": 7}, "conversation_subject"),
            ({"conversation_subject": "x" * 513}, "too long"),
        ):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, message):
                    ViewerSpec("s", "c", "a", **values)

    def test_clean_import_has_no_filesystem_or_terminal_side_effect(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import jsonl_viewer; "
            "assert jsonl_viewer.__all__ == "
            "['ViewerHost', 'ViewerSpec', 'view_jsonl']; print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            # Isolated mode ignores PYTHONPATH; insert the source explicitly.
            if completed.returncode != 0:
                isolated = (
                    f"import sys; sys.path.insert(0, {str(root / 'src')!r}); " + script
                )
                completed = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", isolated],
                    cwd=temporary,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = ViewerSpec(
            "session-01",
            "conversation-01",
            "agent-01",
        )

    def test_simple_goto_verbose_and_close_are_transient_and_read_only(self) -> None:
        source = _source()
        before = hashlib.sha256(source).digest()
        host = FakeHost(("goto\t2", "toggle_mode", "close"))
        view_jsonl(source, self.spec, host)

        self.assertEqual(hashlib.sha256(source).digest(), before)
        self.assertEqual(host.close_calls, 1)
        self.assertGreaterEqual(len(host.frames), 4)
        self.assertIn("Loading immutable JSONL snapshot", host.frames[0])
        simple = host.frames[1]
        self.assertIn("READ ONLY • SIMPLE", simple)
        self.assertIn('"timestamp"', simple)
        self.assertNotIn('"latency_ms"', simple)
        moved = host.frames[2]
        self.assertIn("> 2 │", moved)
        verbose = host.frames[3]
        self.assertIn("READ ONLY • VERBOSE", verbose)
        self.assertIn('"latency_ms": 23', verbose)
        self.assertLess(verbose.index('"timestamp"'), verbose.index('"request_type"'))
        self.assertLess(verbose.index('"request_type"'), verbose.index('"content"'))
        self.assertLess(verbose.index('"content"'), verbose.index('"latency_ms"'))

    def test_search_wraps_and_promotes_hidden_field(self) -> None:
        host = FakeHost(
            (
                "search\tlatency_ms",
                "next_match",
                "previous_match",
                "close",
            ),
            size=(120, 30),
        )
        view_jsonl(_source(), self.spec, host)
        first = host.frames[2]
        self.assertIn("VERBOSE", first)
        self.assertIn("Hidden-field match selected", first)
        self.assertIn("1/3 occurrences • Search all text='latency_ms'", first)
        self.assertIn("@ 1 │", first)
        second = host.frames[3]
        self.assertIn("2/3 occurrences • Search all text='latency_ms'", second)
        self.assertIn("@ 2 │", second)
        previous = host.frames[4]
        self.assertIn("1/3 occurrences • Search all text='latency_ms'", previous)

    def test_search_is_case_insensitive_and_includes_nested_values(self) -> None:
        source = (
            b'{"content":"Alpha top level","nested":{"content":"needle"}}\n'
            b'{"content":"unrelated","nested":{"content":"ALPHA"}}\n'
        )
        host = FakeHost(
            (
                "search\tALPHA",
                "search\tneedle",
                "close",
            ),
            size=(100, 20),
        )
        view_jsonl(source, self.spec, host)
        self.assertIn("1/2 occurrences • Search all text='ALPHA'", host.frames[2])
        self.assertIn("1/1 occurrences • Search all text='needle'", host.frames[3])

    def test_embedded_json_search_uses_the_original_top_level_string(self) -> None:
        content = json.dumps(
            {"message": "line1\nline2"},
            separators=(",", ":"),
        )
        source = (
            json.dumps(
                {
                    "timestamp": "1",
                    "request_type": "response",
                    "content": content,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        host = FakeHost(
            (
                "search\t\\n",
                "clear_search",
                "search\tline1\nline2",
                "close",
            ),
            size=(160, 18),
        )

        view_jsonl(source, self.spec, host)

        self.assertIn("Search all text=", host.frames[2])
        self.assertIn("1/1 occurrences", host.frames[2])
        self.assertIn("⟦", host.frames[2])
        self.assertNotIn("Search all text=", host.frames[4])
        self.assertIn("unsupported controls", host.frames[4])

    def test_non_enumerated_search_finds_values_and_promotes_hidden_field(self) -> None:
        host = FakeHost(("search\tneedle", "close"))
        source = b'{"content":"safe","secret":"needle"}\n'
        view_jsonl(source, self.spec, host)
        frame = host.frames[2]
        self.assertIn("1/1 occurrences • Search all text='needle'", frame)
        self.assertIn("READ ONLY • VERBOSE", frame)
        self.assertIn('"secret": "⟦needle⟧"', frame)

    def test_no_match_position_paging_and_invalid_goto_are_bounded(self) -> None:
        source = (
            b'{"timestamp":"1","request_type":"request","content":"one",'
            b'"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}\n'
            b'{"timestamp":"2","request_type":"response","content":"two"}\n'
        )
        host = FakeHost(
            (
                "search\tabsent",
                "clear_search",
                "toggle_mode",
                "page_down",
                "page_down",
                "page_down",
                "page_down",
                "page_down",
                "page_down",
                "page_down",
                "page_down",
                "goto\tzero",
                "close",
            ),
            size=(80, 9),
        )
        view_jsonl(source, self.spec, host)
        self.assertIn("0/0 occurrences • Search all text='absent'", host.frames[2])
        self.assertIn("VERBOSE", host.frames[4])
        self.assertTrue(
            any("Record 2/2" in frame for frame in host.frames),
            "paging did not cross to the next source record",
        )
        self.assertIn("positive integer", host.frames[-1])

    def test_hidden_match_prevents_hiding_its_selected_field(self) -> None:
        host = FakeHost(
            ("search\t12", "toggle_mode", "close"),
            size=(100, 18),
        )
        view_jsonl(_source(), self.spec, host)
        frame = host.frames[-1]
        self.assertIn("VERBOSE", frame)
        self.assertIn("Verbose mode is required", frame)
        self.assertIn("1/1 occurrences", frame)
        self.assertIn('"latency_ms": ⟦12⟧', frame)

    def test_embedded_projection_pages_by_derived_lines_and_closes_cleanly(
        self,
    ) -> None:
        content = json.dumps(
            {f"field_{index:02}": f"value_{index:02}" for index in range(20)},
            separators=(",", ":"),
        )
        source = (
            json.dumps(
                {
                    "timestamp": "1",
                    "request_type": "response",
                    "content": content,
                },
                separators=(",", ":"),
            )
            + "\n"
            + json.dumps(
                {
                    "timestamp": "2",
                    "request_type": "response",
                    "content": "next record",
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        before = hashlib.sha256(source).digest()
        host = FakeHost(("page_down",) * 16 + ("close",), size=(80, 9))

        view_jsonl(source, self.spec, host)

        self.assertEqual(hashlib.sha256(source).digest(), before)
        self.assertEqual(host.close_calls, 1)
        self.assertTrue(
            any('"field_19": "value_19"' in frame for frame in host.frames),
            "derived projection did not page to its final child",
        )
        self.assertTrue(
            any("Record 2/2" in frame for frame in host.frames),
            "derived line count did not cross to the next source record",
        )

        tiny = FakeHost(("close",), size=(12, 4))
        view_jsonl(source, self.spec, tiny)
        self.assertEqual(tiny.close_calls, 1)
        self.assertEqual(len(tiny.frames[-1].splitlines()), 4)
        self.assertTrue(all(len(line) <= 12 for line in tiny.frames[-1].splitlines()))
        self.assertIn("READ ONLY", tiny.frames[-1])

    def test_cancel_dismisses_search_then_closes_without_persistence(self) -> None:
        first = FakeHost(("search\talpha", "cancel", "close"))
        view_jsonl(_source(), self.spec, first)
        self.assertEqual(first.close_calls, 1)
        self.assertIn("Search cleared", first.frames[-1])

        second = FakeHost(("close",))
        view_jsonl(_source(), self.spec, second)
        self.assertNotIn("Search content", second.frames[-1])

    def test_help_and_eof_always_return_terminal_ownership(self) -> None:
        host = FakeHost(("help", "cancel", None), size=(70, 18))
        view_jsonl(_source(), self.spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertIn("commands never edit, replay, or retry", host.frames[2])

    def test_host_failure_still_closes_view(self) -> None:
        class FailingHost(FakeHost):
            def present(self, frame: str) -> None:
                super().present(frame)
                raise RuntimeError("host failed")

        host = FailingHost()
        with self.assertRaisesRegex(RuntimeError, "host failed"):
            view_jsonl(_source(), self.spec, host)
        self.assertEqual(host.close_calls, 1)

    def test_geometry_failure_still_closes_view_exactly_once(self) -> None:
        class FailingHost(FakeHost):
            def terminal_size(self) -> tuple[int, int]:
                raise RuntimeError("geometry failed")

        host = FailingHost()
        with self.assertRaisesRegex(RuntimeError, "geometry failed"):
            view_jsonl(_source(), self.spec, host)
        self.assertEqual(host.close_calls, 1)

    def test_source_requires_immutable_bytes(self) -> None:
        host = FakeHost()
        with self.assertRaisesRegex(TypeError, "immutable bytes"):
            view_jsonl(bytearray(b"{}\n"), self.spec, host)  # type: ignore[arg-type]
        self.assertEqual(host.close_calls, 0)

class KeyInputTests(unittest.TestCase):
    def setUp(self) -> None:
        from jsonl_viewer._input import parse_jsonl

        self.spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        self.source = _source()
        self.snapshot = parse_jsonl(self.source)

    def step(self, state, event):
        from jsonl_viewer.engine import _transition

        return _transition(state, event, self.snapshot, self.spec, page_size=5, selected_line_count=20)

    def query_state(self):
        from jsonl_viewer._model import ViewState

        state = ViewState()
        for event in ("text\t/",):
            state, closed = self.step(state, event)
            self.assertFalse(closed)
        return state

    def test_input_protocol_defaults_and_exact_host_surface(self) -> None:
        import inspect

        legacy = ViewerSpec("s", "c", "a", "t", "r", "v", "title", "Scope", "Subject")
        self.assertEqual(legacy.input_protocol, "semantic")
        self.assertEqual(list(inspect.signature(ViewerSpec).parameters)[-1], "input_protocol")
        self.assertEqual(inspect.signature(ViewerSpec).parameters["input_protocol"].default, "semantic")
        for invalid in (None, True, 1, "", "KEYS", "other"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "input_protocol"):
                ViewerSpec("s", "c", "a", input_protocol=invalid)
        methods = {name: member for name, member in vars(jsonl_viewer.ViewerHost).items() if not name.startswith("_")}
        self.assertEqual(set(methods), {"terminal_size", "color_enabled", "present", "read_event", "close_view"})
        for name, member in methods.items():
            self.assertEqual(tuple(inspect.signature(member).parameters), ("self", "frame") if name == "present" else ("self",))

    def test_prompt_editing_controls_never_enter_committed_query(self) -> None:
        cases = (
            ("wrong", ("ctrl_u",), "", 0),
            ("abcdef", ("left", "left", "ctrl_u"), "ef", 0),
            ("abcdef", ("home", "right", "ctrl_k"), "a", 1),
            ("alpha beta  ", ("ctrl_w",), "alpha ", 6),
            ("abc", ("left", "backspace"), "ac", 1),
            ("abc", ("home", "delete"), "bc", 0),
            ("abc", ("ctrl_a", "right", "ctrl_e", "left"), "abc", 2),
            ("abc", ("home", "left", "backspace", "end", "right", "delete"), "abc", 3),
            ("개요😀", ("left", "backspace"), "개😀", 1),
        )
        for text, keys, expected, cursor in cases:
            with self.subTest(text=text, keys=keys):
                state, _ = self.step(self.query_state(), "text\t" + text)
                for key in keys:
                    state, closed = self.step(state, "key\t" + key)
                    self.assertFalse(closed)
                self.assertEqual((state.prompt.buffer, state.prompt.cursor), (expected, cursor))
                state, _ = self.step(state, "text\tZ")
                state, _ = self.step(state, "key\tenter")
                self.assertEqual(state.search.query, (expected[:cursor] + "Z" + expected[cursor:]).strip())
                self.assertNotIn("\x15", state.search.query)
                self.assertNotIn("U0015", state.search.query)

    def test_ctrl_u_unicode_regression_finds_seven_records(self) -> None:
        source = b"".join((json.dumps({"content": "개요 " + str(i)}, ensure_ascii=False) + "\n").encode() for i in range(7))
        events = ("text\t/", "text\twrong", "key\tctrl_u", "text\t개", "text\t요", "key\tenter", "close")
        host = FakeHost(events)
        view_jsonl(source, self.spec, host)
        self.assertIn("1/7 occurrences • Search all text='개요'", host.frames[-1])
        self.assertNotIn("U0015", "\n".join(host.frames))
        self.assertNotIn("\x15", "\n".join(host.frames))
        self.assertEqual(host.close_calls, 1)

    def test_unicode_paste_and_navigation_letters_are_literal_in_drafts(self) -> None:
        text = "qjkb nNmh?cg/ 개요😀e\u0301"
        for chunks in ((text,), tuple(text)):
            with self.subTest(chunks=len(chunks)):
                state = self.query_state()
                for chunk in chunks:
                    state, closed = self.step(state, "text\t" + chunk)
                    self.assertFalse(closed)
                self.assertEqual(state.prompt.buffer, text)
                self.assertEqual(state.selected_index, 0)
                state, _ = self.step(state, "key\tenter")
                self.assertEqual(state.search.query, text)
        from jsonl_viewer._model import ViewState
        state, _ = self.step(ViewState(), "text\t/content")
        self.assertEqual(state.prompt.buffer, "content")

    def test_escape_each_draft_preserves_every_committed_state_field(self) -> None:
        from jsonl_viewer._model import SearchState, ViewMode, ViewState
        from jsonl_viewer._search import find_matches

        active = ViewState(selected_index=1, record_line_offset=7, mode=ViewMode.VERBOSE,
                           search=SearchState("alpha", find_matches(self.snapshot, "alpha"), 1),
                           message="prior error", message_is_error=True)
        for start in (("text\t/",), ("text\tg",)):
            for escape in ("key\tescape", "key\tunknown_escape", "cancel"):
                with self.subTest(start=start, escape=escape):
                    state = active
                    for event in (*start, "text\twrong", "text\tdraft"):
                        state, closed = self.step(state, event)
                        self.assertFalse(closed)
                    self.assertIsNotNone(state.prompt)
                    restored, closed = self.step(state, escape)
                    self.assertFalse(closed)
                    self.assertEqual(restored, active)
                    advanced, _ = self.step(restored, "text\tn")
                    self.assertEqual(advanced.search.current_index, 0)
        self.assertEqual(self.source, _source())

    def test_legacy_semantic_and_line_commands_have_matching_actions(self) -> None:
        commands = ((" j ", "cursor_down"), ("down", "down"), ("k", "cursor_up"), ("up", "up"),
                    ("h", "cursor_left"), ("l", "cursor_right"), ("fold", "toggle_fold"),
                    ("pgdn", "page_down"), ("pgup", "page_up"), ("g 2", "goto\t2"),
                    ("/ alpha", "search\talpha"), ("n", "next_match"),
                    ("N", "previous_match"), ("m", "toggle_mode"), ("help", "help"),
                    ("?", "help"), ("esc", "cancel"), ("c", "clear_search"),
                    ("clear", "clear_search"), ("q", "close"), ("quit", "close"),
                    ("", "unknown"), ("/content alpha", "unknown"), ("g x", "goto\tx"),
                    ("/ content alpha\tbeta", "search\tcontent alpha\tbeta"))
        from jsonl_viewer._model import ViewState
        for command, semantic in commands:
            with self.subTest(command=command):
                self.assertEqual(self.step(ViewState(), "line\t" + command), self.step(ViewState(), semantic))
        events = tuple(value for _, value in commands)
        keyed, legacy = FakeHost(events), FakeHost(events)
        view_jsonl(self.source, self.spec, keyed)
        view_jsonl(self.source, dataclasses.replace(self.spec, input_protocol="semantic"), legacy)
        self.assertEqual(keyed.frames, legacy.frames)
        host = FakeHost(("line\tq", "text\tq", "key\teof", "close"))
        view_jsonl(self.source, dataclasses.replace(self.spec, input_protocol="semantic"), host)
        self.assertEqual(len(host.frames), 5)

    def test_prompt_limits_malformed_transport_and_controls_are_transactional(self) -> None:
        from jsonl_viewer._model import ViewState
        for begin, limit in [(("text\t/",), 1024), (("text\tg",), 128)]:
            state = ViewState()
            for event in begin:
                state, _ = self.step(state, event)
            state, _ = self.step(state, "text\t" + "x" * limit)
            self.assertEqual(len(state.prompt.buffer), limit)
            failed, closed = self.step(state, "text\ty")
            self.assertFalse(closed)
            self.assertEqual(failed.prompt.buffer, state.prompt.buffer)
            self.assertIsNotNone(failed.prompt.error)
            repaired, _ = self.step(failed, "key\tctrl_u")
            self.assertEqual(repaired.prompt.buffer, "")
        initial = self.query_state()
        for event in (17, b"q", "text\t" + "x" * 8193, "text\t\x15", "text\t\t", "text\t\n", "text\t\x1b", "text\t\u202e", "text\t\ud800", "line\tq\n"):
            with self.subTest(event=repr(event)[:40]):
                state, closed = self.step(initial, event)
                self.assertFalse(closed)
                self.assertEqual(state.prompt.buffer, "")
                self.assertIsNotNone(state.prompt.error)
        for event in ("key\tunknown", "key\tclose", "key\tup", "key\tbogus", "search\talpha"):
            state, closed = self.step(initial, event)
            self.assertFalse(closed)
            self.assertEqual(state, initial)
        # Payload limits exclude the five-character line envelope.
        accepted, closed = self.step(ViewState(), "line\t" + " " * 8191 + "q")
        self.assertTrue(closed)
        rejected, closed = self.step(ViewState(), "line\t" + " " * 8192 + "q")
        self.assertFalse(closed)
        self.assertTrue(rejected.message_is_error)

    def test_prompt_eof_interrupt_and_failure_close_once_without_submitting(self) -> None:
        for begin in (("text\t/",), ("text\tg",)):
            for ending in (None, "key\teof", "key\tinterrupt"):
                with self.subTest(begin=begin, ending=ending):
                    host = FakeHost((*begin, "text\tdraft", ending))
                    view_jsonl(self.source, self.spec, host)
                    self.assertEqual(host.close_calls, 1)
                    self.assertNotIn("Search all text=", host.frames[-1])
        class FailingRead(FakeHost):
            def read_event(self):
                event = super().read_event()
                if event == "fail":
                    raise OSError("read failed")
                return event
        host = FailingRead(("text\t/", "fail"))
        with self.assertRaisesRegex(OSError, "read failed"):
            view_jsonl(self.source, self.spec, host)
        self.assertEqual(host.close_calls, 1)
        from jsonl_viewer._model import ViewState
        state, _ = self.step(ViewState(), "search\talpha")
        cancelled, closed = self.step(state, "key\tinterrupt")
        self.assertFalse(closed)
        self.assertIsNone(cancelled.search)

    def test_goto_and_search_validation_allow_correction_before_commit(self) -> None:
        from jsonl_viewer._model import ViewState

        state, _ = self.step(ViewState(), "text\tg")
        for event in ("text\twrong", "key\tenter"):
            state, closed = self.step(state, event)
            self.assertFalse(closed)
        self.assertEqual(state.selected_index, 0)
        self.assertIsNotNone(state.prompt.error)
        for event in ("key\tctrl_u", "text\t2", "key\tenter"):
            state, closed = self.step(state, event)
            self.assertFalse(closed)
        self.assertIsNone(state.prompt)
        self.assertEqual(state.selected_index, 1)
        for event in ("text\t/", "key\tenter"):
            state, _ = self.step(state, event)
        self.assertIsNone(state.search)
        self.assertIn("must not be empty", state.prompt.error)
        self.assertEqual(state.selected_index, 1)
        for event in ("text\talpha", "key\tenter"):
            state, _ = self.step(state, event)
        self.assertIsNone(state.prompt)
        self.assertEqual(tuple(hit.record_index for hit in state.search.occurrences), (0, 1))
        self.assertEqual(state.selected_index, 0)

    def test_help_empty_and_malformed_sources_cannot_open_prompts(self) -> None:
        for source, events in ((b"", ("text\t/", "text\tg", "close")),
                               (b"{bad}\n", ("text\t/", "text\tg", "line\t/ content alpha", "close")),
                               (self.source, ("text\t?", "text\t/", "text\tg", "close"))):
            host = FakeHost(events)
            view_jsonl(source, self.spec, host)
            self.assertEqual(host.close_calls, 1)
            self.assertFalse(any("Search query: " in frame or "Go to source line: " in frame for frame in host.frames))

    def test_prompt_rendering_is_bounded_plain_color_equivalent_and_transient(self) -> None:
        from jsonl_viewer._render import strip_ansi, _text_cells

        events = ("text\t/", "text\t" + "개요😀e\u0301" * 60, "key\thome", "key\tright", "key\tend", "key\tescape", "close")
        for size in ((12, 4), (32, 8), (80, 24)):
            with self.subTest(size=size):
                plain, color = FakeHost(events, size=size), FakeHost(events, size=size, color=True)
                view_jsonl(self.source, self.spec, plain)
                view_jsonl(self.source, self.spec, color)
                if size[1] == 24:
                    self.assertEqual([without_caret(frame) for frame in plain.frames],
                                     [strip_ansi(frame) for frame in color.frames])
                for frame in [*plain.frames, *(strip_ansi(frame) for frame in color.frames)]:
                    self.assertLessEqual(len(frame.splitlines()), size[1])
                    self.assertTrue(all(_text_cells(line) <= size[0] for line in frame.splitlines()))
                    self.assertNotIn("\x1b", frame)
                self.assertEqual(plain.frames[1], plain.frames[-1])
        reopened = FakeHost(("close",))
        view_jsonl(self.source, self.spec, reopened)
        self.assertNotIn("Search query: ", reopened.frames[-1])


if __name__ == "__main__":
    unittest.main()
