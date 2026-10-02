"""Bounded input diagnostics and deterministic semantic rendering tests."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
import unittest
from unittest import mock

import jsonl_viewer._json as json_module
from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._input import (
    MAX_RECORD_BYTES,
    MAX_RECORDS,
    MAX_NESTING_DEPTH,
    MAX_SOURCE_BYTES,
    MAX_VALUE_NODES,
    parse_jsonl,
)
from jsonl_viewer._model import ViewMode
from jsonl_viewer._render import _format_record, record_line_count, strip_ansi

from tests.support import FakeHost, styled_text, without_caret


class InputDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = ViewerSpec("s", "c", "a")

    def _frame(self, source: bytes) -> str:
        host = FakeHost(("close",), size=(100, 16))
        view_jsonl(source, self.spec, host)
        self.assertEqual(host.close_calls, 1)
        return host.frames[-1]

    def test_invalid_utf8_is_content_safe_and_line_correlated(self) -> None:
        frame = self._frame(b'{}\n{"content":"private"}\xff\n')
        self.assertIn("not strict UTF-8", frame)
        self.assertIn("source line 2", frame.lower())
        self.assertNotIn("private", frame)

    def test_malformed_duplicate_blank_and_nonfinite_records_are_diagnostic(
        self,
    ) -> None:
        cases = (
            (b'{"content":}\n', "malformed"),
            (b'{"content":"a","content":"b"}\n', "duplicate field"),
            (b"{}\n\n", "blank record"),
            (b'{"content":NaN}\n', "invalid JSON value"),
            (b'{"content":1e999}\n', "invalid JSON value"),
        )
        for source, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self._frame(source))

    def test_empty_snapshot_is_a_read_only_state(self) -> None:
        frame = self._frame(b"")
        self.assertIn("empty immutable JSONL snapshot", frame)
        self.assertIn("? help • q close", frame)

    def test_source_record_and_count_bounds_are_reported(self) -> None:
        self.assertIn(
            str(MAX_SOURCE_BYTES),
            self._frame(b" " * (MAX_SOURCE_BYTES + 1)),
        )
        oversized = json.dumps("x" * MAX_RECORD_BYTES).encode("utf-8") + b"\n"
        self.assertIn(str(MAX_RECORD_BYTES), self._frame(oversized))
        too_many = b"{}\n" * (MAX_RECORDS + 1)
        self.assertIn(str(MAX_RECORDS), self._frame(too_many))

    def test_nesting_and_value_node_bounds_are_reported(self) -> None:
        too_deep = (
            "[" * MAX_NESTING_DEPTH + "0" + "]" * MAX_NESTING_DEPTH + "\n"
        ).encode("ascii")
        self.assertIn(str(MAX_NESTING_DEPTH), self._frame(too_deep))

        too_many_values = (
            "[" + ",".join("0" for _ in range(MAX_VALUE_NODES)) + "]\n"
        ).encode("ascii")
        self.assertIn(str(MAX_VALUE_NODES), self._frame(too_many_values))


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = ViewerSpec(
            "session\x1b[31m",
            "대화🙂",
            "agent\u202ehidden",
        )
        self.source = (
            json.dumps(
                {
                    "timestamp": "2026-08-29",
                    "request_type": "response",
                    "content": "line1\nline2\x1b[2J\u202e",
                    "number": 7,
                    "flag": True,
                    "nothing": None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    def _content_source(self, content: str, **extra: object) -> bytes:
        record = {
            "timestamp": "2026-08-29T09:00:00Z",
            "request_type": "response",
            "content": content,
            **extra,
        }
        return (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    def _projection(
        self,
        content: str,
        *,
        mode: ViewMode = ViewMode.SIMPLE,
    ) -> tuple[str, object]:
        snapshot = parse_jsonl(self._content_source(content))
        lines, facts = _format_record(
            snapshot.records[0],
            ViewerSpec("s", "c", "a"),
            mode,
        )
        logical = "\n".join(
            "".join(segment.text for segment in line.segments) for line in lines
        )
        return logical, facts

    def test_control_characters_are_visible_text_not_terminal_controls(self) -> None:
        host = FakeHost(("toggle_mode", "close"), size=(120, 24))
        view_jsonl(self.source, self.spec, host)
        frame = host.frames[-1]
        self.assertNotIn("\x1b", frame)
        self.assertIn(r"\u001b", frame)
        self.assertIn(r"\u202e", frame)
        self.assertIn(r"line1\nline2", frame)

    def test_scope_label_id_and_subject_are_projected_at_each_width_tier(self) -> None:
        source = b'{"content":"value"}\n'
        cases = (
            (
                (180, 12),
                ViewerSpec(
                    "session",
                    "conversation-full-id",
                    "agent",
                    conversation_subject="A concise subject",
                ),
                "Conversation: conversation-full-id — A concise subject",
            ),
            (
                (88, 12),
                ViewerSpec(
                    "s",
                    "debate-full-id",
                    "a",
                    conversation_label="Debate",
                    conversation_subject="Provider diagnostics",
                ),
                "Debate: debate-full-id — Provider diagnostics",
            ),
            (
                (47, 8),
                ViewerSpec(
                    "s",
                    "d1",
                    "a",
                    conversation_label="Debate",
                    conversation_subject="topic",
                ),
                "S:s • Debate: d1 — topic • A:a",
            ),
        )
        for size, spec, expected in cases:
            with self.subTest(size=size):
                host = FakeHost(("close",), size=size)
                view_jsonl(source, spec, host)
                self.assertIn(expected, host.frames[-1])

    def test_scope_header_controls_are_neutralized(self) -> None:
        spec = ViewerSpec(
            "s",
            "d\x1b[2J",
            "a",
            conversation_label="Debate\nkind",
            conversation_subject="subject\u202ehidden",
        )
        host = FakeHost(("close",), size=(180, 10))
        view_jsonl(b'{"content":"value"}\n', spec, host)
        frame = host.frames[-1]
        self.assertNotIn("\x1b", frame)
        self.assertIn(
            r"Debate\u000akind: d\u001b[2J — subject\u202ehidden",
            frame,
        )

    def test_ansi_and_plain_have_exact_semantic_text_apart_from_caret(self) -> None:
        plain = FakeHost(("toggle_mode", "close"), size=(120, 24), color=False)
        ansi = FakeHost(("toggle_mode", "close"), size=(120, 24), color=True)
        view_jsonl(self.source, self.spec, plain)
        view_jsonl(self.source, self.spec, ansi)
        self.assertEqual(strip_ansi(ansi.frames[-1]), without_caret(plain.frames[-1]))
        self.assertIn("timestamp", styled_text(ansi.frames[-1], "34"))
        self.assertIn("2026", styled_text(ansi.frames[-1], "32"))
        self.assertIn("7", styled_text(ansi.frames[-1], "35"))
        self.assertIn("true", styled_text(ansi.frames[-1], "33"))
        self.assertIn("null", styled_text(ansi.frames[-1], "33"))

    def test_story_like_nested_json_is_structured_and_semantically_colored(
        self,
    ) -> None:
        inner = {
            "path": 'C:\\tmp\\"quoted"',
            "message": "line1\nline2\x1b[2J\u202e",
            "items": [7, True, None],
        }
        outer = {
            "kind": "provider_response",
            "response_text": json.dumps(
                inner,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        source = self._content_source(
            json.dumps(outer, ensure_ascii=False, separators=(",", ":"))
        )
        spec = ViewerSpec("s", "c", "a")

        plain = FakeHost(("close",), size=(240, 30), color=False)
        ansi = FakeHost(("close",), size=(240, 30), color=True)
        view_jsonl(source, spec, plain)
        view_jsonl(source, spec, ansi)
        frame = plain.frames[-1]
        colored = ansi.frames[-1]

        self.assertIn('"content": [expanded JSON string ×1] {', frame)
        self.assertIn('"response_text": [expanded JSON string ×1] {', frame)
        self.assertIn('"path": ' + json.dumps(inner["path"]), frame)
        self.assertNotIn(r"\"response_text\"", frame)
        self.assertNotIn("\x1b", frame)
        self.assertIn(r"line1\nline2\u001b[2J\u202e", frame)
        self.assertIn("JSON display 2 expanded, 0 skipped, 0 truncated", frame)
        self.assertEqual(strip_ansi(colored), without_caret(frame))
        self.assertIn("response_text", styled_text(colored, "34"))
        self.assertIn("provider_response", styled_text(colored, "32"))
        self.assertIn("7", styled_text(colored, "35"))
        self.assertIn("true", styled_text(colored, "33"))
        self.assertIn("null", styled_text(colored, "33"))
        self.assertIn("[expanded JSON string ×1] ", styled_text(colored, "90"))

    def test_only_complete_strict_object_or_array_strings_expand(self) -> None:
        accepted = (
            json.dumps({"value": 1}),
            json.dumps([1, 2]),
            "{}",
            "[]",
            ' \t\n {"value":1} \r ',
        )
        for content in accepted:
            with self.subTest(accepted=content):
                logical, facts = self._projection(content)
                self.assertIn("[expanded JSON string ×1]", logical)
                self.assertEqual(facts.expanded_strings, 1)
                self.assertEqual(facts.skipped_expansions, 0)

        rejected = (
            'prefix {"value":1}',
            '{"value":1} trailing',
            json.dumps("text"),
            "7",
            "true",
            "null",
            '{"value":}',
            '{"value":1,"value":2}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
            '{"value":1e999}',
        )
        for content in rejected:
            with self.subTest(rejected=content):
                logical, facts = self._projection(content)
                self.assertNotIn("[expanded JSON string", logical)
                self.assertNotIn("[JSON expansion skipped", logical)
                self.assertEqual(facts.expanded_strings, 0)
                self.assertEqual(facts.skipped_expansions, 0)

    def test_invalid_encoded_child_stays_text_while_valid_siblings_expand(
        self,
    ) -> None:
        content = json.dumps(
            {
                "valid": json.dumps({"ok": [1]}),
                "invalid": '{"value":NaN}',
            },
            separators=(",", ":"),
        )
        logical, facts = self._projection(content)
        self.assertIn('"valid": [expanded JSON string ×1] {', logical)
        self.assertIn('"invalid": "{\\"value\\":NaN}"', logical)
        self.assertEqual(facts.expanded_strings, 2)
        self.assertEqual(facts.skipped_expansions, 0)

    def test_projection_preserves_source_bytes_and_original_value_types(self) -> None:
        content = json.dumps(
            {"response_text": json.dumps({"answer": [1, 2, 3]})},
            separators=(",", ":"),
        )
        source = self._content_source(content)
        before_hash = hashlib.sha256(source).digest()
        snapshot = parse_jsonl(source)
        before_value = copy.deepcopy(snapshot.records[0].value)
        self.assertIsInstance(snapshot.records[0].value, dict)
        assert isinstance(snapshot.records[0].value, dict)
        self.assertIsInstance(snapshot.records[0].value["content"], str)

        _format_record(
            snapshot.records[0],
            ViewerSpec("s", "c", "a"),
            ViewMode.SIMPLE,
        )

        self.assertEqual(snapshot.records[0].value, before_value)
        self.assertIsInstance(snapshot.records[0].value["content"], str)
        self.assertEqual(hashlib.sha256(source).digest(), before_hash)

    def test_encoding_layer_and_projected_depth_boundaries_are_exact(self) -> None:
        encoded = json.dumps({"leaf": "end"}, separators=(",", ":"))
        for _ in range(json_module.MAX_EXPANSION_LAYERS - 1):
            encoded = json.dumps(encoded, separators=(",", ":"))
        logical, facts = self._projection(encoded)
        self.assertIn("[expanded JSON string ×16]", logical)
        self.assertIn('"leaf": "end"', logical)
        self.assertEqual(facts.expanded_strings, 1)
        self.assertEqual(facts.skipped_expansions, 0)

        over_layer = json.dumps(encoded, separators=(",", ":"))
        logical, facts = self._projection(over_layer)
        self.assertIn("[JSON expansion skipped: encoding layer limit]", logical)
        self.assertNotIn('\n    "leaf": "end"', logical)
        self.assertEqual(facts.expanded_strings, 0)
        self.assertEqual(facts.skipped_expansions, 1)

        relative_depth_63: object = 0
        for _ in range(62):
            relative_depth_63 = [relative_depth_63]
        logical, facts = self._projection(json.dumps(relative_depth_63))
        self.assertIn("[expanded JSON string ×1]", logical)
        self.assertEqual(facts.skipped_expansions, 0)

        relative_depth_64: object = [relative_depth_63]
        logical, facts = self._projection(json.dumps(relative_depth_64))
        self.assertIn("[JSON expansion skipped: display depth limit]", logical)
        self.assertEqual(facts.expanded_strings, 0)
        self.assertEqual(facts.skipped_expansions, 1)

    def test_transactional_node_and_decoded_byte_limits_use_record_budget(
        self,
    ) -> None:
        self.assertEqual(json_module.MAX_EXPANSION_LAYERS, 16)
        self.assertEqual(json_module.MAX_PROJECTED_DISPLAY_DEPTH, 64)
        self.assertEqual(json_module.MAX_DERIVED_VALUE_NODES, 200_000)
        self.assertEqual(
            json_module.MAX_CUMULATIVE_DECODED_UTF8_BYTES,
            16_777_216,
        )

        first = json.dumps({"ok": 1}, separators=(",", ":"))
        second = json.dumps({"secret": 2}, separators=(",", ":"))
        outer = json.dumps(
            {"first": first, "second": second},
            separators=(",", ":"),
        )
        with mock.patch.object(json_module, "MAX_DERIVED_VALUE_NODES", 5):
            logical, facts = self._projection(outer)
        self.assertIn('"first": [expanded JSON string ×1] {', logical)
        self.assertIn('"ok": 1', logical)
        self.assertIn(
            '"second": [JSON expansion skipped: derived node limit]',
            logical,
        )
        self.assertNotIn('\n      "secret": 2', logical)
        self.assertEqual(facts.expanded_strings, 2)
        self.assertEqual(facts.skipped_expansions, 1)

        first_with_preview = json.dumps(
            {"long": "x" * 4_097},
            separators=(",", ":"),
        )
        aggregate_outer = json.dumps(
            {"first": first_with_preview, "second": second},
            separators=(",", ":"),
        )
        host = FakeHost(("close",), size=(240, 16))
        with mock.patch.object(json_module, "MAX_DERIVED_VALUE_NODES", 5):
            view_jsonl(
                self._content_source(aggregate_outer),
                ViewerSpec("s", "c", "a"),
                host,
            )
        self.assertIn(
            "JSON display 2 expanded, 1 skipped, 1 truncated "
            "(4096/4097 UTF-8 bytes retained)",
            host.frames[-1],
        )

        exact_byte_budget = len(outer.encode("utf-8")) + len(first.encode("utf-8"))
        with mock.patch.object(
            json_module,
            "MAX_CUMULATIVE_DECODED_UTF8_BYTES",
            exact_byte_budget,
        ):
            logical, facts = self._projection(outer)
        self.assertIn('"first": [expanded JSON string ×1] {', logical)
        self.assertIn(
            '"second": [JSON expansion skipped: decoded byte limit]',
            logical,
        )
        self.assertNotIn('\n      "secret": 2', logical)
        self.assertEqual(facts.expanded_strings, 2)
        self.assertEqual(facts.skipped_expansions, 1)

    def test_encoded_container_expands_before_preview_and_preserves_structure(
        self,
    ) -> None:
        value = {f"field_{index:04}": "v" for index in range(600)}
        value["k" * 5_000] = "tail"
        encoded = json.dumps(value, separators=(",", ":"))
        self.assertGreater(len(encoded.encode("utf-8")), 4_096)

        logical, facts = self._projection(encoded)
        snapshot = parse_jsonl(self._content_source(encoded))
        line_count = record_line_count(
            snapshot.records[0],
            ViewerSpec("s", "c", "a"),
            ViewMode.SIMPLE,
        )

        self.assertIn("[expanded JSON string ×1] {", logical)
        self.assertIn('"field_0599": "v"', logical)
        self.assertIn(json.dumps("k" * 5_000) + ': "tail"', logical)
        self.assertTrue(logical.endswith("}"))
        self.assertNotIn("[truncated", logical)
        self.assertEqual(line_count, len(logical.splitlines()))
        self.assertEqual(facts.expanded_strings, 1)
        self.assertEqual(facts.truncated_leaves, 0)

    def test_nested_leaf_previews_are_independent_visible_and_utf8_complete(
        self,
    ) -> None:
        encoded = json.dumps(
            {
                "ascii": "x" * 4_097,
                "korean": "한" * 1_366,
                "emoji": "🙂" * 1_025,
                "number": 7,
                "flag": True,
                "nothing": None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        logical, facts = self._projection(encoded)
        self.assertIn("[truncated 4096/4097 UTF-8 bytes]", logical)
        self.assertIn("[truncated 4095/4098 UTF-8 bytes]", logical)
        self.assertIn("[truncated 4096/4100 UTF-8 bytes]", logical)
        self.assertIn('"number": 7', logical)
        self.assertIn('"flag": true', logical)
        self.assertIn('"nothing": null', logical)
        self.assertEqual(facts.expanded_strings, 1)
        self.assertEqual(facts.truncated_leaves, 3)
        self.assertEqual(facts.truncated_retained_utf8_bytes, 12_287)
        self.assertEqual(facts.truncated_full_utf8_bytes, 12_295)

        host = FakeHost(("close",), size=(240, 20))
        view_jsonl(
            self._content_source(encoded),
            ViewerSpec("s", "c", "a"),
            host,
        )
        frame = host.frames[-1]
        self.assertIn('[truncated 4096/4097 UTF-8 bytes] "x', frame)
        self.assertIn(
            "JSON display 1 expanded, 0 skipped, 3 truncated "
            "(12287/12295 UTF-8 bytes retained)",
            frame,
        )

    def test_verbose_leaf_preview_and_mode_footer_facts_use_distinct_limit(
        self,
    ) -> None:
        encoded = json.dumps(
            {"large": "x" * 65_537},
            separators=(",", ":"),
        )
        logical, facts = self._projection(encoded, mode=ViewMode.VERBOSE)
        self.assertIn("[truncated 65536/65537 UTF-8 bytes]", logical)
        self.assertEqual(facts.truncated_leaves, 1)

        mode_value = json.dumps(
            {"large": "x" * 5_000},
            separators=(",", ":"),
        )
        host = FakeHost(("toggle_mode", "close"), size=(240, 12))
        view_jsonl(
            self._content_source(mode_value),
            ViewerSpec("s", "c", "a"),
            host,
        )
        self.assertIn("JSON display 1 expanded, 0 skipped, 1 truncated", host.frames[1])
        self.assertIn("READ ONLY • VERBOSE", host.frames[-1])
        self.assertIn(
            "JSON display 1 expanded, 0 skipped, 0 truncated", host.frames[-1]
        )

    def test_isolated_surrogates_have_visible_complete_bounded_previews(self) -> None:
        logical, facts = self._projection(json.dumps({"leaf": "before\ud800after"}))
        self.assertIn(r'"before\ud800after"', logical)
        logical.encode("utf-8", errors="strict")
        self.assertEqual(facts.truncated_leaves, 0)

        for value, mode, retained, complete in (
            ("x" * 4090 + "\ud800", ViewMode.SIMPLE, 4096, 4096),
            ("x" * 4095 + "\ud800", ViewMode.SIMPLE, 4095, 4101),
            ("한" * 1364 + "\udfff", ViewMode.SIMPLE, 4092, 4098),
            ("🙂" * 1023 + "\ud800tail", ViewMode.SIMPLE, 4092, 4102),
            ("x" * 65535 + "\ud800", ViewMode.VERBOSE, 65535, 65541),
        ):
            with self.subTest(mode=mode, complete=complete):
                logical, facts = self._projection(json.dumps({"leaf": value}), mode=mode)
                logical.encode("utf-8", errors="strict")
                self.assertNotRegex(logical, r"[\ud800-\udfff]")
                if retained == complete:
                    self.assertIn(r"\ud800", logical)
                    self.assertEqual(facts.truncated_leaves, 0)
                else:
                    self.assertIn(f"[truncated {retained}/{complete} UTF-8 bytes]", logical)
                    self.assertEqual(facts.truncated_leaves, 1)
                    self.assertEqual(facts.truncated_retained_utf8_bytes, retained)
                    self.assertEqual(facts.truncated_full_utf8_bytes, complete)
                    self.assertNotIn(r"\ud800", logical)
                    self.assertNotIn(r"\udfff", logical)

    def test_simple_content_preview_is_bounded_at_complete_utf8_boundary(self) -> None:
        content = "한" * 2_000
        source = (
            json.dumps(
                {
                    "timestamp": "t",
                    "request_type": "response",
                    "content": content,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        host = FakeHost(("close",), size=(120, 15))
        view_jsonl(source, ViewerSpec("s", "c", "a"), host)
        frame = host.frames[-1]
        self.assertIn("content preview 4095/6000 UTF-8 bytes", frame)
        self.assertIn("width clipped", frame)

    def test_verbose_string_preview_uses_its_distinct_bound(self) -> None:
        source = (
            json.dumps(
                {
                    "timestamp": "t",
                    "request_type": "response",
                    "content": "x" * 65_537,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        host = FakeHost(("toggle_mode", "cancel", "close"), size=(120, 15))
        view_jsonl(source, ViewerSpec("s", "c", "a"), host)
        self.assertIn("content preview 65536/65537 UTF-8 bytes", host.frames[-1])

    def test_tiny_terminal_retains_read_only_and_help_close_cues(self) -> None:
        host = FakeHost(("close",), size=(28, 6))
        view_jsonl(self.source, self.spec, host)
        frame = host.frames[-1]
        lines = frame.splitlines()
        self.assertLessEqual(len(lines), 6)
        self.assertTrue(all(len(line) <= 28 for line in lines))
        self.assertIn("READ ONLY", frame)
        self.assertIn("? help • q close", frame)

    def test_unicode_cell_geometry_is_bounded_at_minimum_size(self) -> None:
        host = FakeHost(("close",), size=(12, 4))
        view_jsonl(self.source, self.spec, host)
        frame = host.frames[-1]

        def cells(value: str) -> int:
            return sum(
                0
                if unicodedata.combining(character)
                else 2
                if unicodedata.east_asian_width(character) in {"W", "F"}
                else 1
                for character in value
            )

        lines = frame.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(cells(line) <= 12 for line in lines))
        self.assertIn("READ ONLY", frame)

    def test_search_markers_are_textual_in_plain_mode(self) -> None:
        source = (
            b'{"timestamp":"1","request_type":"response","content":"hit"}\n'
            b'{"timestamp":"2","request_type":"response","content":"hit"}\n'
        )
        host = FakeHost(("search\thit", "close"), size=(100, 30))
        view_jsonl(source, ViewerSpec("s", "c", "a"), host)
        frame = host.frames[-1]
        self.assertIn("@ 1 │", frame)
        self.assertIn("* 2 │", frame)
        self.assertIn("⟦active⟧ • n/N", frame)

        ansi = FakeHost(
            ("search\thit", "close"),
            size=(100, 30),
            color=True,
        )
        view_jsonl(source, ViewerSpec("s", "c", "a"), ansi)
        colored = ansi.frames[-1]
        self.assertEqual(strip_ansi(colored), without_caret(frame))
        self.assertIn("\x1b[1;36m@\x1b[0m", colored)
        self.assertIn("\x1b[2;90m*\x1b[0m", colored)
        self.assertIn("hit", styled_text(colored, "43"))
        self.assertNotIn("\x1b[1;30;43m@", colored)


if __name__ == "__main__":
    unittest.main()
