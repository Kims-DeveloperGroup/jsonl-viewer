"""Bounded input diagnostics and deterministic semantic rendering tests."""

from __future__ import annotations

import json
import re
import unicodedata
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._input import (
    MAX_RECORD_BYTES,
    MAX_RECORDS,
    MAX_NESTING_DEPTH,
    MAX_SOURCE_BYTES,
    MAX_VALUE_NODES,
)
from jsonl_viewer._render import strip_ansi

from tests.support import FakeHost


class InputDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = ViewerSpec("s", "c", "a", ("content",))

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
        self.assertIn("h help • q close", frame)

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
            ("content", "number", "flag", "nothing"),
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
                    ("content",),
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
                    ("content",),
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
                    ("content",),
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
            ("content",),
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

    def test_ansi_and_plain_have_exact_semantic_text_equivalence(self) -> None:
        plain = FakeHost(("toggle_mode", "close"), size=(120, 24), color=False)
        ansi = FakeHost(("toggle_mode", "close"), size=(120, 24), color=True)
        view_jsonl(self.source, self.spec, plain)
        view_jsonl(self.source, self.spec, ansi)
        self.assertEqual(strip_ansi(ansi.frames[-1]), plain.frames[-1])
        self.assertRegex(ansi.frames[-1], re.compile(r"\x1b\[34m.*timestamp"))
        self.assertRegex(ansi.frames[-1], re.compile(r"\x1b\[32m.*2026"))
        self.assertRegex(ansi.frames[-1], re.compile(r"\x1b\[35m7"))
        self.assertRegex(ansi.frames[-1], re.compile(r"\x1b\[33mtrue"))
        self.assertRegex(ansi.frames[-1], re.compile(r"\x1b\[33mnull"))

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
        view_jsonl(source, ViewerSpec("s", "c", "a", ("content",)), host)
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
        view_jsonl(source, ViewerSpec("s", "c", "a", ("content",)), host)
        self.assertIn("content preview 65536/65537 UTF-8 bytes", host.frames[-1])

    def test_tiny_terminal_retains_read_only_and_help_close_cues(self) -> None:
        host = FakeHost(("close",), size=(28, 6))
        view_jsonl(self.source, self.spec, host)
        frame = host.frames[-1]
        lines = frame.splitlines()
        self.assertLessEqual(len(lines), 6)
        self.assertTrue(all(len(line) <= 28 for line in lines))
        self.assertIn("READ ONLY", frame)
        self.assertIn("h help • q close", frame)

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
        host = FakeHost(("search\tcontent\thit", "close"), size=(100, 30))
        view_jsonl(source, ViewerSpec("s", "c", "a", ("content",)), host)
        frame = host.frames[-1]
        self.assertIn("@ 1 │", frame)
        self.assertIn("* 2 │", frame)
        self.assertIn("@ current, * other", frame)

        ansi = FakeHost(
            ("search\tcontent\thit", "close"),
            size=(100, 30),
            color=True,
        )
        view_jsonl(source, ViewerSpec("s", "c", "a", ("content",)), ansi)
        colored = ansi.frames[-1]
        self.assertEqual(strip_ansi(colored), frame)
        self.assertIn("\x1b[1;30;43m@\x1b[0m", colored)
        self.assertIn("\x1b[4;33m*\x1b[0m", colored)


if __name__ == "__main__":
    unittest.main()
