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

from tests.support import FakeHost


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
            ["content", "ok"],  # type: ignore[arg-type]  # Runtime normalization.
        )
        self.assertEqual(spec.searchable_fields, ("content", "ok"))
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
            (),
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

    def test_spec_rejects_ambiguous_or_unbounded_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            ViewerSpec("s", "c", "a", ("content", "content"))
        with self.assertRaisesRegex(ValueError, "control separator"):
            ViewerSpec("s", "c", "a", ("bad\tfield",))
        with self.assertRaisesRegex(ValueError, "distinct"):
            ViewerSpec(
                "s",
                "c",
                "a",
                (),
                date_time_field="same",
                request_type_field="same",
            )

    def test_spec_validates_distinct_scope_header_parts(self) -> None:
        spec = ViewerSpec(
            "session",
            "d20260829T090000_abcd1234",
            "agent",
            (),
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
                    ViewerSpec("s", "c", "a", (), **values)

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
            ("timestamp", "request_type", "content", "latency_ms", "ok"),
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

    def test_search_is_closed_wraps_and_promotes_hidden_field(self) -> None:
        host = FakeHost(
            (
                "search\tlatency_ms\t2",
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
        self.assertIn("Search latency_ms='2' • 1/2", first)
        self.assertIn("@ 1 │", first)
        second = host.frames[3]
        self.assertIn("Search latency_ms='2' • 2/2", second)
        self.assertIn("@ 2 │", second)
        previous = host.frames[4]
        self.assertIn("Search latency_ms='2' • 1/2", previous)

    def test_search_is_case_insensitive_and_top_level_only(self) -> None:
        source = (
            b'{"content":"Alpha top level","nested":{"content":"needle"}}\n'
            b'{"content":"unrelated","nested":{"content":"ALPHA"}}\n'
        )
        host = FakeHost(
            (
                "search\tcontent\tALPHA",
                "search\tcontent\tneedle",
                "close",
            ),
            size=(100, 20),
        )
        view_jsonl(source, self.spec, host)
        self.assertIn("Search content='ALPHA' • 1/1", host.frames[2])
        self.assertIn("Search content='needle' • 0/0", host.frames[3])

    def test_non_enumerated_search_does_not_inspect_values(self) -> None:
        host = FakeHost(("search\tsecret\tneedle", "close"))
        source = b'{"content":"safe","secret":"needle"}\n'
        view_jsonl(source, self.spec, host)
        frame = host.frames[2]
        self.assertIn("Field is not searchable", frame)
        self.assertNotIn("needle", frame)

    def test_no_match_position_paging_and_invalid_goto_are_bounded(self) -> None:
        source = (
            b'{"timestamp":"1","request_type":"request","content":"one",'
            b'"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}\n'
            b'{"timestamp":"2","request_type":"response","content":"two"}\n'
        )
        host = FakeHost(
            (
                "search\tcontent\tabsent",
                "clear_search",
                "toggle_mode",
                "page_down",
                "page_down",
                "page_down",
                "goto\tzero",
                "close",
            ),
            size=(80, 9),
        )
        view_jsonl(source, self.spec, host)
        self.assertIn("Search content='absent' • 0/0", host.frames[2])
        self.assertIn("VERBOSE", host.frames[4])
        self.assertTrue(
            any("Record 2/2" in frame for frame in host.frames),
            "paging did not cross to the next source record",
        )
        self.assertIn("positive integer", host.frames[-1])

    def test_hidden_match_prevents_hiding_its_selected_field(self) -> None:
        host = FakeHost(
            ("search\tlatency_ms\t12", "toggle_mode", "close"),
            size=(100, 18),
        )
        view_jsonl(_source(), self.spec, host)
        frame = host.frames[-1]
        self.assertIn("VERBOSE", frame)
        self.assertIn("Verbose mode is required", frame)
        self.assertIn("Search latency_ms='12' • 1/1", frame)

    def test_cancel_dismisses_search_then_closes_without_persistence(self) -> None:
        first = FakeHost(("search\tcontent\talpha", "cancel", "close"))
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


if __name__ == "__main__":
    unittest.main()
