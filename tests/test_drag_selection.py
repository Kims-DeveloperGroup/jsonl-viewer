"""Copy actual painted JSON ranges without transferring terminal ownership."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import unittest
from unittest import mock

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer import _cell_layout, _render, engine
from jsonl_viewer._render import _text_cells, strip_ansi
from jsonl_viewer.inspection import Group, Inspection, Section
from tests.support import FakeHost, styled_text


def _source(*values):
    return "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values).encode()


@dataclass(frozen=True)
class _Mark:
    name: str


@dataclass(frozen=True)
class _Point:
    text: str
    needle: str
    offset: int = 0
    button: int = 0
    phase: str = "press"
    saved: str | None = None

    def __call__(self, host):
        for row, line in enumerate(strip_ansi(host.frames[-1]).splitlines(), 1):
            if self.text in line and self.needle in line:
                column = _text_cells(line[:line.index(self.needle)]) + self.offset + 1
                if self.saved is not None:
                    host.points[self.saved] = (column, row)
                return f"mouse\t{self.button}\t{column}\t{row}\t{self.phase}"
        raise AssertionError(f"Point {self!r} absent from painted frame:\n{host.frames[-1]}")


def _saved(name, *, button=0, phase="release"):
    def event(host):
        column, row = host.points[name]
        return f"mouse\t{button}\t{column}\t{row}\t{phase}"
    return event


def _edge(direction, *, button=32, phase="press"):
    def event(host):
        column, row = host.points["anchor"]
        if direction == "bottom":
            row = host._size[1]
        elif direction == "top":
            row = 1
        elif direction == "right":
            column = host._size[0]
        elif direction == "left":
            column = 1
        return f"mouse\t{button}\t{column}\t{row}\t{phase}"
    return event


class _CopyHost(FakeHost):
    """Script physical input against painted frames and record clipboard writes."""

    def __init__(self, events, *, result=True, available=True, **kwargs):
        super().__init__(events, **kwargs)
        self.result = result
        self.copied = []
        self.points = {}
        self.marks = {}
        self.mark_polling = {}
        self.polling = []
        if not available:
            self.copy_to_clipboard = None

    def read_event(self):
        event = next(self._events, None)
        while isinstance(event, _Mark):
            self.marks[event.name] = self.frames[-1]
            self.mark_polling[event.name] = self.polling[-1] if self.polling else None
            event = next(self._events, None)
        if isinstance(event, tuple):
            self._size = event
            return "idle"
        return event(self) if callable(event) else event

    def copy_to_clipboard(self, text):
        self.copied.append(text)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def set_autoscroll_active(self, active):
        self.polling.append(active)


class DragSelectionTests(unittest.TestCase):
    def _view(self, values, events, *, size=(100, 24), color=False,
              inspection=None, result=True, available=True):
        source = _source(*values)
        digest = hashlib.sha256(source).digest()
        host = _CopyHost((*events, "close"), size=size, color=color,
                         result=result, available=available)
        spec = ViewerSpec("s", "c", "a", input_protocol="keys", inspection=inspection)
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        return host

    def test_forward_reverse_ranges_copy_exact_projected_unicode_and_escapes(self):
        for color in (False, True):
            for reverse in (False, True):
                for value, start, end, expected in (
                    ("alpha beta gamma", "alpha", "beta", "alpha beta"),
                    ("ab界e\u0301Zcd", "界", "Z", "界e\u0301Z"),
                    ("alpha\nbeta", "alpha", "beta", "alpha\\nbeta"),
                ):
                    with self.subTest(color=color, reverse=reverse, value=value):
                        first = _Point('"content"', start)
                        last = _Point('"content"', end, _text_cells(end) - 1)
                        if reverse:
                            first, last = last, first
                        host = self._view(({"content": value},), (
                            _Mark("before"), first,
                            _Point(last.text, last.needle, last.offset, button=32, saved="end"),
                            _Mark("selected"), _saved("end"), _Mark("copied"),
                            "idle", _Mark("idle"), _saved("end"), _Mark("duplicate"),
                            "text\tj", _Mark("navigated"),
                        ), color=color)
                        self.assertEqual(host.copied, [expected])
                        copied = strip_ansi(host.marks["copied"])
                        self.assertIn(f"Copied {len(expected)} characters.", copied)
                        self.assertIn(f"Copied {len(expected)} characters.",
                                      strip_ansi(host.marks["idle"]))
                        self.assertNotIn("\x1b", host.copied[0])
                        self.assertNotIn("Copied", strip_ansi(host.marks["navigated"]))
                        if color:
                            self.assertIn(expected, styled_text(host.marks["selected"], "106"))
                        else:
                            self.assertNotIn("\x1b", host.marks["selected"])
                            self.assertIn("Selecting", host.marks["selected"])

    def test_plain_click_and_same_wide_glyph_motion_never_copy(self):
        for extra in (0, 1):
            with self.subTest(extra=extra):
                host = self._view(({"content": "ab界cd"},), (
                    _Point('"content"', "界", saved="anchor"),
                    _Point('"content"', "界", extra, button=32, saved="end"),
                    _saved("end"), "idle",
                ))
                self.assertEqual(host.copied, [])
                self.assertFalse(any("Copied" in strip_ansi(frame) for frame in host.frames))

    def test_real_drag_returning_to_anchor_copies_one_complete_glyph(self):
        host = self._view(({"content": "界ab"},), (
            _Point('"content"', "界", saved="anchor"),
            _Point('"content"', "a", button=32),
            _saved("anchor", button=32, phase="press"), _saved("anchor"),
            _Mark("copied"),
        ))
        self.assertEqual(host.copied, ["界"])
        self.assertIn("Copied 1 characters.", strip_ansi(host.marks["copied"]))

    def test_multiline_selection_copies_indentation_and_newlines_without_gutters(self):
        for color in (False, True):
            for reverse in (False, True):
                with self.subTest(color=color, reverse=reverse):
                    start = _Point('"zero"', '"zero"', -4)
                    end = _Point('"one"', '"one",', len('"one",') - 1)
                    if reverse:
                        start, end = end, start
                    host = self._view(({"content": ["zero", "one", "two"]},), (
                        start, _Point(end.text, end.needle, end.offset, button=32, saved="end"),
                        _saved("end"),
                    ), color=color)
                    self.assertEqual(host.copied, ['    "zero",\n    "one",'])

    def test_search_highlight_brackets_are_excluded_but_literal_brackets_copy(self):
        value = "alpha [literal] ⟦source⟧ beta"
        host = self._view(({"content": value},), (
            "text\t/", "text\talpha", "key\tenter",
            _Point('"content"', "alpha"),
            _Point('"content"', "beta", 3, button=32, saved="end"),
            _saved("end"),
        ), color=True)
        self.assertEqual(host.copied, [value])

    def test_folded_placeholder_copies_without_hidden_descendants(self):
        host = self._view(({"content": {"nested": {"hidden": "DO-NOT-COPY"},
                                         "tail": "keep"}},), (
            _Point('"nested"', "{"), "key\tenter",
            _Point('"nested"', '"nested"'),
            _Point('"nested"', "{...}", 4, button=32, saved="end"),
            _saved("end"),
        ))
        self.assertEqual(host.copied, ['"nested": {...}'])

    def test_projected_preview_and_expansion_cues_copy_without_expanding_source(self):
        for value, cue in (
            ("a" * 6000, "[truncated 4096/6000 UTF-8 bytes]"),
            (json.dumps({"nested": 1}), "[expanded JSON string ×1]"),
        ):
            with self.subTest(cue=cue):
                host = self._view(({"content": value},), (
                    _Point('"content"', cue),
                    _Point('"content"', cue, _text_cells(cue) - 1,
                           button=32, saved="end"), _saved("end"),
                ))
                self.assertEqual(host.copied, [cue])

    def test_stationary_vertical_edge_extends_selection_and_stops_at_document_end(self):
        values = {"content": [f"row-{index:02}" for index in range(24)]}
        host = self._view((values,), (
            _Point('"row-00"', "row-00", saved="anchor"), _edge("bottom"),
            *("idle" for _ in range(60)), _Mark("end"),
            _edge("bottom", button=0, phase="release"), _Mark("copied"),
            "idle", _Mark("steady"),
        ), size=(55, 14))
        self.assertEqual(len(host.copied), 1)
        self.assertIn("row-23", host.copied[0])
        self.assertTrue(host.copied[0].startswith("row-00"))
        self.assertNotIn("│", host.copied[0])
        self.assertIn(True, host.polling)
        self.assertIs(host.mark_polling["end"], False)
        self.assertIs(host.polling[-1], False)
        self.assertIn("Copied", strip_ansi(host.marks["steady"]))

    def test_stationary_horizontal_edge_copies_unclipped_projected_text(self):
        value = "abcdefghijklmnopqrstuvwxyz" * 6
        host = self._view(({"content": value},), (
            _Point('"content"', "abc", saved="anchor"), _edge("right"),
            *("idle" for _ in range(30)), _Mark("scrolled"),
            _edge("right", button=0, phase="release"),
        ), size=(44, 16), color=True)
        self.assertEqual(len(host.copied), 1)
        self.assertGreater(len(host.copied[0]), 44)
        self.assertTrue(value.startswith(host.copied[0]), host.copied[0])
        self.assertNotIn("…", host.copied[0])
        self.assertIn(True, host.polling)

    def test_stationary_left_edge_extends_reverse_selection_and_restores_polling(self):
        value = "begin-" + "0123456789" * 15 + "-tail-end"
        host = self._view(({"content": value},), (
            *("scroll_right" for _ in range(30)),
            _Point("tail-end", "tail-end", 7, saved="anchor"), _edge("left"),
            *("idle" for _ in range(130)), _Mark("start"),
            _edge("left", button=0, phase="release"),
        ), size=(44, 16))
        self.assertEqual(host.copied, ['  "content": "' + value])
        self.assertIn(True, host.polling)
        self.assertIs(host.mark_polling["start"], False)

    def test_inspection_single_split_drag_capture_retains_item_and_section(self):
        inspection = Inspection(groups=(
            Group("selected", "Selected item", (3, 1, 2), sections=(
                Section("shown", "Shown", (3, 1)), Section("hidden", "Hidden", (2,)))),
            Group("other", "Other item", (4,)),
        ))
        records = ({"content": "A-copy-me"}, {"content": "B-forbidden"},
                   {"content": "C-copy-me"}, {"content": "D-forbidden"})
        for split in (False, True):
            for color in (False, True):
                with self.subTest(split=split, color=color):
                    events = (["text\tv"] if split else []) + [
                        _Mark("before"), _Point('"content"', "A-copy-me", saved="anchor"),
                        _edge("bottom"), *("idle" for _ in range(30)),
                        _edge("bottom", button=0, phase="release"), _Mark("after"),
                        "key\ttab", _Mark("list"),
                    ]
                    host = self._view(records, events, inspection=inspection,
                                      size=(160, 20), color=color)
                    self.assertEqual(len(host.copied), 1)
                    self.assertIn("C-copy-me", host.copied[0])
                    self.assertIn("A-copy-me", host.copied[0])
                    self.assertLess(host.copied[0].index("A-copy-me"),
                                    host.copied[0].index("C-copy-me"))
                    self.assertNotIn("forbidden", host.copied[0])
                    after = strip_ansi(host.marks["after"])
                    self.assertIn("selected · a annotations", after)
                    self.assertNotIn("D-forbidden", after)
                    self.assertIn("Copied", after)
                    self.assertIn("Focus: List", strip_ansi(host.marks["list"]))

    def test_drag_release_on_header_copies_without_opening_header_controls(self):
        host = self._view(({"content": "alpha beta"},), (
            _Point('"content"', "alpha", saved="anchor"),
            _Point('"content"', "beta", 3, button=32),
            lambda host: "mouse\t0\t1\t1\trelease", _Mark("after"),
        ), inspection=Inspection())
        self.assertEqual(len(host.copied), 1)
        self.assertNotIn("Error:", strip_ansi(host.marks["after"]))
        self.assertIn('"content": "alpha beta"', strip_ansi(host.marks["after"]))

    def test_completed_selection_does_not_leak_into_another_inspection_item(self):
        inspection = Inspection(groups=(Group("first", "First", (1,)),
                                         Group("second", "Second", (2,))))
        for split in (False, True):
            with self.subTest(split=split):
                host = self._view(({"content": "alpha beta"}, {"content": "second-copy"}), (
                    *(("text\tv",) if split else ()),
                    _Point('"content"', "alpha"),
                    _Point('"content"', "beta", 3, button=32, saved="end"),
                    _saved("end"), "key\tdown", _Mark("new-item"),
                    _Point('"content"', "second"),
                    _Point('"content"', "copy", 3, button=32, saved="end"),
                    _saved("end"),
                ), inspection=inspection, size=(160, 24), color=True)
                self.assertEqual(host.copied, ["alpha beta", "second-copy"])
                self.assertIn("second · a annotations", strip_ansi(host.marks["new-item"]))
                self.assertNotIn("Copied", strip_ansi(host.marks["new-item"]))

    def test_cancellation_does_not_copy_or_turn_release_into_navigation(self):
        for cancel in ("key\tescape", "text\tj", "text\tm", "text\t/", "help", (60, 15), "close", None):
            with self.subTest(cancel=cancel):
                host = self._view(({"content": "alpha beta"},), (
                    _Point('"content"', "alpha"),
                    _Point('"content"', "beta", 3, button=32, saved="end"),
                    cancel, _saved("end"), "idle",
                ))
                self.assertEqual(host.copied, [])
                self.assertFalse(any("Copied" in strip_ansi(frame) for frame in host.frames))

    def test_copy_outcomes_are_truthful_and_remain_visible_on_compact_idle(self):
        for inspection in (None, Inspection()):
            for result, available, notice in (
                (True, True, "Copied 3 characters."),
                (False, True, "Copy failed."),
                (RuntimeError("not available"), True, "Copy failed."),
                (True, False, "Clipboard unavailable."),
            ):
                with self.subTest(inspection=inspection, available=available, notice=notice):
                    host = self._view(("abcdef",), (
                        _Point("abcdef", "a"),
                        _Point("abcdef", "c", button=32, saved="end"),
                        _saved("end"), _Mark("copied"), "idle", _Mark("idle"),
                    ), size=(30, 14), inspection=inspection,
                       result=result, available=available)
                    for mark in ("copied", "idle"):
                        self.assertIn(notice, strip_ansi(host.marks[mark]))
                    self.assertEqual(host.copied, ["abc"] if available else [])
                    if notice != "Copied 3 characters.":
                        self.assertNotIn("Copied", strip_ansi(host.marks["copied"]))

    def test_drag_motion_reuses_projection_and_does_not_parse_again(self):
        for value, setup in (("abcdefghijklmnopqrstuvwxyz" * 200, ()),
                             ("abc" + "界e\u0301" * 8000 + "tail", ("text\tm",))):
            with self.subTest(unicode=bool(setup)):
                def start_count(host):
                    host.width_before = widths.call_count
                    return _Point('"content"', "abc", 2, button=32, saved="end")(host)

                def finish_count(host):
                    host.width_after = widths.call_count
                    return _saved("end")(host)

                host = _CopyHost((
                    *setup, _Point('"content"', "abc"),
                    _Point('"content"', "abc", 1, button=32), start_count,
                    *(_Point('"content"', "abc", index % 3, button=32, saved="end")
                      for index in range(20)),
                    finish_count, "close",
                ), size=(100, 24))
                with mock.patch.object(engine, "parse_jsonl", wraps=engine.parse_jsonl) as parse, \
                        mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats, \
                        mock.patch.object(_cell_layout, "character_cells",
                                          wraps=_cell_layout.character_cells) as widths:
                    view_jsonl(_source({"content": value}),
                               ViewerSpec("s", "c", "a", input_protocol="keys"), host)
                self.assertEqual(parse.call_count, 1)
                self.assertEqual(formats.call_count, 1 + len(setup))
                self.assertLess(host.width_after - host.width_before, len(value))
                self.assertEqual(len(host.copied), 1)

    def test_input_failure_during_edge_drag_clears_polling_and_closes_once(self):
        def fail(host):
            raise RuntimeError("input transport failed")

        host = _CopyHost((
            _Point('"row-00"', "row-00", saved="anchor"), _edge("bottom"), fail,
        ), size=(55, 14))
        source = _source({"content": [f"row-{i:02}" for i in range(30)]})
        with self.assertRaisesRegex(RuntimeError, "input transport failed"):
            view_jsonl(source, ViewerSpec("s", "c", "a", input_protocol="keys"), host)
        self.assertIn(True, host.polling)
        self.assertIs(host.polling[-1], False)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(host.copied, [])


if __name__ == "__main__":
    unittest.main()
