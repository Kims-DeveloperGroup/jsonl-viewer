"""Separate record identity from expanded, mode-local JSON row positions."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
import re
import unittest
from unittest import mock

from jsonl_viewer import ViewerSpec
from jsonl_viewer import _render
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import FoldIdentity, ViewMode, ViewState
from jsonl_viewer._mouse import MouseEvent, hit_test
from jsonl_viewer._render import _text_cells, strip_ansi
from tests.support import without_caret
from tests.test_cursor_and_folding import _View, _source
from tests.test_navigation_cache import _Navigation


def _labels(frame):
    labels = []
    for row in strip_ansi(frame.text).splitlines():
        gutter, separator, _ = row.partition("│")
        match = re.fullmatch(r"[>@* ]\s*(?:R\s*(\d+))?\s*L\s*(\d+|–)\s*", gutter)
        if separator and match:
            record, line = match.groups()
            labels.append((int(record) if record is not None else None,
                           None if line == "–" else int(line)))
    return labels


def _mixed_source():
    return _source(0, {"content": {"items": [10, 20]}, "debug": True},
                   json.dumps({"leaf": [True, False]}))


def _focused(frame):
    return next(cell for cell in frame.characters if cell.position == frame.cursor)


class RecordLineGutterTests(unittest.TestCase):
    def test_global_rows_are_mode_specific_and_records_label_only_first_visible_row(self):
        source = _mixed_source()
        for mode, expected in (
            (ViewMode.SIMPLE, [(1, 1), (2, 2), (None, 3), (None, 4), (None, 5),
                               (None, 6), (None, 7), (None, 8), (None, 9), (3, 10),
                               (None, 11), (None, 12), (None, 13), (None, 14), (None, 15)]),
            (ViewMode.VERBOSE, [(1, 1), (2, 2), (None, 3), (None, 4), (None, 5),
                                (None, 6), (None, 7), (None, 8), (None, 9), (None, 10),
                                (3, 11), (None, 12), (None, 13), (None, 14),
                                (None, 15), (None, 16)]),
        ):
            frames = []
            for color in (False, True):
                with self.subTest(mode=mode, color=color):
                    view = _Navigation(parse_jsonl(source), ViewerSpec("s", "c", "a"),
                                       state=ViewState(mode=mode), size=(120, 40), color=color)
                    self.assertEqual(_labels(view.frame), expected)
                    self.assertEqual([record.source_line for record in view.snapshot.records], [1, 2, 3])
                    self.assertEqual(view.snapshot.records[1].value["content"], {"items": [10, 20]})
                    frames.append(without_caret(strip_ansi(view.frame.text)))
            self.assertEqual(*frames)

    def test_folded_openers_keep_expanded_line_numbers_and_leave_gaps(self):
        cases = (
            (("content", "items"), [(1, 1), (2, 2), (None, 3), (None, 4), (None, 8),
                                    (None, 9), (3, 10), (None, 11), (None, 12),
                                    (None, 13), (None, 14), (None, 15)]),
            ((), [(1, 1), (2, 2), (3, 10), (None, 11), (None, 12),
                  (None, 13), (None, 14), (None, 15)]),
        )
        for color in (False, True):
            for path, expected in cases:
                with self.subTest(color=color, path=path):
                    view = _Navigation(parse_jsonl(_mixed_source()), ViewerSpec("s", "c", "a"),
                                       state=ViewState(folds=frozenset({FoldIdentity(1, path)})),
                                       session=_render.RenderSession(), size=(120, 40), color=color)
                    self.assertEqual(_labels(view.frame), expected)
                    self.assertIn("[...]" if path else "{...}", strip_ansi(view.frame.text))
                    view.state = replace(view.state, folds=frozenset())
                    self.assertEqual(_labels(view.render())[9], (3, 10))

    def test_paging_relabels_mid_record_first_row_and_preserves_next_record_prefix(self):
        for color in (False, True):
            with self.subTest(color=color):
                view = _Navigation(parse_jsonl(_source({"content": list(range(12))}, 99)),
                                   ViewerSpec("s", "c", "a"), session=_render.RenderSession(),
                                   size=(64, 10), color=color)
                self.assertEqual(_labels(view.frame)[0], (1, 1))
                saw_mid_record = False
                for _ in range(20):
                    labels = _labels(view.step("page_down"))
                    self.assertEqual(labels[0][0], view.state.selected_index + 1)
                    self.assertEqual([record for record, _ in labels if record is not None],
                                     list(dict.fromkeys(record for record, _ in labels
                                                        if record is not None)))
                    if view.state.selected_index == 1:
                        self.assertEqual(labels[0], (2, 17))
                        break
                    saw_mid_record = True
                    self.assertGreater(labels[0][1], 1)
                    self.assertTrue(all(record is None for record, line in labels[1:]
                                        if line < 17))
                else:
                    self.fail("paging did not reach the next record")
                self.assertTrue(saw_mid_record)
                previous = view.step("page_up")
                self.assertEqual(_labels(previous)[0][0], 1)
                self.assertGreater(_labels(previous)[0][1], 1)

    def test_auxiliary_raw_and_normalized_excerpt_rows_do_not_consume_line_numbers(self):
        for value, query, expected in (
            ({"payload": {"z": 2, "a": True}}, '{"a":true,"z":2}',
             [(1, 1), (None, 2), (None, 3), (None, 4), (None, 5), (None, 6),
              (None, None), (None, None), (2, 7)]),
            ({"payload": json.dumps({"text": "line1\nline2"})}, ': ',
             [(1, 1), (None, 2), (None, 3), (None, 4), (None, 5),
              (None, None), (None, None), (2, 6)]),
        ):
            for color in (False, True):
                with self.subTest(query=query, color=color):
                    view = _View(_source(value, 99), size=(160, 40), color=color)
                    frame = view.step("search\t" + query)
                    self.assertEqual(_labels(frame), expected)
                    self.assertEqual(strip_ansi(frame.text).count("L–"), 2)
                    self.assertEqual(len(view.state.search.occurrences), 1)

    def test_goto_keeps_original_source_lines_instead_of_record_or_display_numbers(self):
        view = _Navigation(parse_jsonl(_mixed_source()), ViewerSpec("s", "c", "a"),
                           session=_render.RenderSession(), size=(120, 40))
        for source_line, index, first_label in ((2, 1, (2, 2)), (3, 2, (3, 10)), (1, 0, (1, 1))):
            frame = view.step(f"goto\t{source_line}")
            self.assertEqual(view.state.selected_index, index)
            self.assertEqual(_labels(frame)[0], first_label)
            self.assertIn(f"source line {source_line}", frame.text)
        view.step("goto\t10")
        self.assertEqual(view.state.message, "Source line 10 is not a JSONL record.")
        self.assertFalse(view.state.message_is_error)
        self.assertEqual(view.state.selected_index, 0)

    def test_lazy_prefix_counts_are_reused_across_warm_moves_modes_and_context_rebinds(self):
        snapshot = parse_jsonl(_source(*({"content": [1, 2], "debug": True} for _ in range(80))))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        session = _render.RenderSession()
        self.assertEqual((session._line_counts, session._line_prefixes), ({}, {}))
        with mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats:
            view = _Navigation(snapshot, spec, session=session,
                               state=ViewState(selected_index=70), size=(80, 12))
            self.assertEqual(_labels(view.frame)[0], (71, 421))
            calls = Counter((id(call.args[0]), call.args[2]) for call in formats.call_args_list)
            self.assertTrue(all(calls[id(record), ViewMode.SIMPLE] == 1
                                for record in snapshot.records[:70]))
            cold_formats = formats.call_count
            for event in ("text\tl", "text\th", "idle"):
                view.step(event)
            self.assertEqual(formats.call_count, cold_formats)
            self.assertEqual(_labels(view.step("toggle_mode"))[0], (71, 491))
            preceding_ids = {id(record) for record in snapshot.records[:70]}
            preceding_before = Counter((id(call.args[0]), call.args[2])
                                       for call in formats.call_args_list
                                       if id(call.args[0]) in preceding_ids)
            self.assertEqual(_labels(view.step("toggle_mode"))[0], (71, 421))
            preceding_after = Counter((id(call.args[0]), call.args[2])
                                      for call in formats.call_args_list
                                      if id(call.args[0]) in preceding_ids)
            self.assertEqual(preceding_after, preceding_before)
        for mode in (ViewMode.SIMPLE, ViewMode.VERBOSE):
            self.assertLessEqual(len(session._line_counts[mode]), len(snapshot.records))
            self.assertLessEqual(len(session._line_prefixes[mode]), len(snapshot.records) + 1)
        fresh = _Navigation(snapshot, spec, state=view.state, size=view.size)
        self.assertEqual((view.frame.text, view.frame.characters), (fresh.frame.text, fresh.frame.characters))
        other = parse_jsonl(_source(0, 1))
        session.bind(spec, other)
        self.assertEqual(session.line_start(1, spec, ViewMode.SIMPLE), 1)
        alternate = ViewerSpec("s", "c", "a", content_field="debug")
        session.bind(alternate, snapshot)
        self.assertEqual(session.line_start(1, alternate, ViewMode.SIMPLE), 3)

    def test_digit_width_and_resize_rebase_cursor_preference_mouse_and_fixed_pan_gutter(self):
        source = _source(*("ABCD界e\u0301🙂Z" * 20 for _ in range(10)))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        for color in (False, True):
            with self.subTest(color=color):
                view = _Navigation(parse_jsonl(source), spec, state=ViewState(selected_index=8),
                                   session=_render.RenderSession(), size=(60, 4), color=color)
                before = view.step("text\tllll")
                self.assertEqual(_focused(before).text, "D")
                preferred = view.state.preferred_column - before.gutter_columns
                after = view.step("text\tj")
                self.assertEqual(_labels(after)[0], (10, 10))
                self.assertEqual(after.gutter_columns, before.gutter_columns + 2)
                self.assertEqual(_focused(after).text, "D")
                self.assertEqual(view.state.preferred_column - after.gutter_columns, preferred)
                self.assertEqual(_focused(view.step("text\tk")).text, "D")
                view.step("text\tj")
                view.size = (40, 4)
                compact = view.render()
                self.assertEqual(compact.gutter_columns, after.gutter_columns - 4)
                self.assertEqual(_focused(compact).text, "D")
                self.assertEqual(view.state.preferred_column - compact.gutter_columns, preferred)
                fixed_gutter = next(row.partition("│")[0] for row in strip_ansi(compact.text).splitlines()
                                    if "│" in row)
                panned = view.step("scroll_right")
                self.assertEqual(next(row.partition("│")[0] for row in strip_ansi(panned.text).splitlines()
                                      if "│" in row), fixed_gutter)
                target = next(cell for cell in panned.characters if cell.text == "界")
                clicked = view.step(f"mouse\t0\t{target.screen_column + 2}\t{target.screen_row + 1}\tpress")
                self.assertEqual(clicked.cursor, target.position)
                self.assertEqual(_focused(clicked).text, "界")
                for cell in clicked.characters:
                    self.assertGreaterEqual(cell.screen_column, clicked.gutter_columns)
                    for extra in range(cell.screen_width):
                        self.assertEqual(hit_test(MouseEvent(0, cell.screen_column + extra + 1,
                                                           cell.screen_row + 1, "press"), clicked), cell)
                plain = strip_ansi(clicked.text).splitlines()
                if not color:
                    self.assertEqual(plain[target.screen_row + 1], " " * target.screen_column + "^")
                self.assertTrue(all(_text_cells(row) <= 40 for row in plain))
                self.assertLessEqual(len(plain), 4)

