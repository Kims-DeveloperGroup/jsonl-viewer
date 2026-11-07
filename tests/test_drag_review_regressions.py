"""Independent regressions for the reviewed drag-selection boundary defects."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
import re
import unittest
from unittest import mock

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer import _render, engine
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import (
    FoldIdentity, SearchState, SelectionPoint, TextSelection, ViewMode, ViewState,
)
from jsonl_viewer._render import render_frame, strip_ansi
from jsonl_viewer._search import find_matches
from jsonl_viewer.inspection import Inspection
from tests.support import styled_text
from tests.test_drag_selection import _CopyHost, _Mark, _Point, _edge, _saved, _source


class DragReviewRegressionTests(unittest.TestCase):
    def _view(self, values, events, *, size=(100, 24), spec=None,
              motion_only=False, session=None):
        source = _source(*values)
        digest = hashlib.sha256(source).digest()
        host = _CopyHost((*events, "close"), size=size, color=True)
        if motion_only:
            host.set_autoscroll_active = None
        spec = spec or ViewerSpec("s", "c", "a", input_protocol="keys")
        with (mock.patch.object(engine, "RenderSession", return_value=session)
              if session is not None else nullcontext()):
            view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        return host

    def test_compact_inspection_footer_cannot_start_a_drag_on_unpainted_json(self):
        spec = ViewerSpec("s", "c", "a", inspection=Inspection(), input_protocol="keys")
        for split, size, value in (
            (False, (100, 8), "HIDDEN-SOURCE-UNPAINTED-JSON"),
            (False, (100, 10), {"a": "HIDDEN-SOURCE-UNPAINTED-JSON"}),
            (True, (160, 12), {"a": "visible-aaa", "b": "visible-bbb",
                              "c": "HIDDEN-SOURCE-UNPAINTED-JSON"}),
        ):
            with self.subTest(split=split):
                def footer_press(host):
                    if split:
                        _Point('"b"', "visible-bbb", saved="last-painted")(host)
                        column, row = host.points["last-painted"]
                        host.points["footer"] = (column, row + 1)
                    elif size[1] == 10:
                        _Point("{", "{", saved="visible-root")(host)
                        column, row = host.points["visible-root"]
                        host.points["footer"] = (column + len('  "a": "'), row + 1)
                    else:
                        host.points["footer"] = (20, size[1])
                    return _saved("footer", phase="press")(host)

                def footer_motion(host):
                    column, row = host.points["footer"]
                    return f"mouse\t32\t{column + 4}\t{row}\tpress"

                events = [*(('text\tv',) if split else ()), _Mark("before"),
                          footer_press, footer_motion, _saved("footer"), _Mark("after")]
                host = self._view((value,), events, size=size, spec=spec)
                self.assertNotIn("HIDDEN-SOURCE", strip_ansi(host.marks["before"]))
                self.assertEqual(host.copied, [])
                self.assertNotIn(True, host.polling)
                self.assertNotIn("Copied", strip_ansi(host.marks["after"]))

    def test_compact_drag_probe_counts_only_composed_painted_rows(self):
        spec = ViewerSpec("s", "c", "a", inspection=Inspection(), input_protocol="keys")
        value = {"a": "visible-aaa", "b": "visible-bbb",
                 "c": "hidden-ccc", "d": "hidden-ddd"}

        def footer_motion(host):
            column, _ = host.points["anchor"]
            host.points["footer"] = (column, host._size[1])
            return _saved("footer", button=32, phase="press")(host)

        host = self._view((value,), (
            "text\tv", _Point('"b"', "visible-bbb", saved="anchor"),
            footer_motion, _Mark("moved"), _saved("footer"), _Mark("copied"),
        ), size=(160, 12), spec=spec)
        expected = 'visible-bbb",\n  "c": "h'
        moved = strip_ansi(host.marks["moved"])
        self.assertIn("hidden-ccc", moved)
        self.assertNotIn("hidden-ddd", moved)
        self.assertIn(f"{len(expected)} characters", moved)
        self.assertEqual(host.copied, [expected])

    def test_upward_drag_from_first_visible_row_starts_before_endpoint_changes(self):
        def first_visible(host):
            for line in strip_ansi(host.frames[-1]).splitlines():
                match = re.search(r'"row-(\d{2})"', line)
                if match:
                    host.anchor_number = int(match[1])
                    return _Point(match[0], f"row-{match[1]}", saved="anchor")(host)
            raise AssertionError("No projected row is visible")

        host = self._view(({"content": [f"row-{i:02}" for i in range(40)]},), (
            "page_down", "page_down", first_visible,
            _edge("top"), *("idle" for _ in range(8)), _Mark("above"),
            _edge("top", button=0, phase="release"),
        ), size=(55, 14))
        self.assertGreater(host.anchor_number, 8)
        self.assertEqual(len(host.copied), 1)
        self.assertIn(f"row-{host.anchor_number - 1:02}", host.copied[0])
        visible = [int(number) for number in re.findall(r'"row-(\d{2})"',
                                                        strip_ansi(host.marks["above"]))]
        self.assertLess(min(visible), host.anchor_number - 3)

    def test_motion_only_borrowed_host_scrolls_one_bounded_step_per_report(self):
        host = self._view(({"content": [f"row-{i:02}" for i in range(45)]},), (
            _Point('"row-00"', "row-00", saved="anchor"), _Mark("before"),
            _edge("bottom"), _Mark("one-step"),
            *(_edge("bottom") for _ in range(23)), _Mark("many-steps"),
            _edge("bottom", button=0, phase="release"),
        ), size=(55, 14), motion_only=True)
        maxima = [max(map(int, re.findall(r'"row-(\d{2})"', strip_ansi(host.marks[name]))))
                  for name in ("before", "one-step", "many-steps")]
        self.assertEqual(maxima[1], maxima[0] + 1)
        self.assertLessEqual(maxima[2], maxima[0] + 24)
        self.assertGreater(maxima[2], 20)
        self.assertEqual(len(host.copied), 1)
        self.assertIn("row-20", host.copied[0])

    def test_viewport_ellipsis_is_not_a_press_target_or_copied_endpoint(self):
        value = "abcdefghijklmnopqrstuvwxyz" * 4
        orphan = self._view(({"content": value},), (
            _Point('"content"', "…", saved="anchor"),
            _Point('"content"', "abc", button=32, saved="end"), _saved("end"),
        ), size=(35, 16))
        self.assertEqual(orphan.copied, [])

        captured = self._view(({"content": value},), (
            _Mark("before"), _Point('"content"', "abc"),
            _Point('"content"', "abc", 1, button=32),
            _Point('"content"', "…", phase="release"), _Mark("copied"),
        ), size=(35, 16))
        line = next(line for line in strip_ansi(captured.marks["before"]).splitlines()
                    if '"content"' in line)
        expected = re.search(r'"content": "(.*?)…', line)[1]
        self.assertEqual(captured.copied, [expected])
        self.assertEqual(styled_text(captured.marks["copied"], "106"), expected)

        literal = self._view(({"content": "A…B"},), (
            _Point('"content"', "A…B"),
            _Point('"content"', "A…B", 2, button=32, saved="end"), _saved("end"),
        ))
        self.assertEqual(literal.copied, ["A…B"])

    def test_partial_wide_glyph_padding_cannot_start_a_selection(self):
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        probe = render_frame(spec, ViewState(), snapshot=parse_jsonl(_source("z" * 200)),
                             diagnostic=None, columns=35, rows=16, color=True)
        step = probe.body_width // 2
        value = "a" * (step - 2) + "界" + "Z" * 100
        host = self._view((value,), (
            "scroll_right", _Mark("panned"),
            _Point("ZZ", "ZZ", -1, saved="anchor"),
            _Point("ZZ", "ZZ", button=32, saved="end"), _saved("end"),
        ), size=(35, 16), spec=spec)
        line = next(line for line in strip_ansi(host.marks["panned"]).splitlines() if "ZZ" in line)
        self.assertTrue(line.partition("│")[2].startswith(" "), line)
        self.assertNotIn("界", line)
        self.assertEqual(host.copied, [])

    def test_large_selection_updates_do_not_rebuild_intermediate_record_projections(self):
        records = [f"r{i:03}-xxxxxxxx" for i in range(100)]
        expected = "\n\n".join(json.dumps(record) for record in records)[1:-1]
        session = _render.RenderSession()
        probe = mock.patch.object(_render, "_format_record", wraps=_render._format_record)
        counter = None

        def begin(host):
            nonlocal counter
            counter = probe.start()
            return _Point("r099", "r099-xxxxxxxx", len(records[-1]) - 2,
                          button=32, saved="end")(host)

        def finish(host):
            host.projection_builds = counter.call_count
            probe.stop()
            return _saved("end")(host)

        try:
            host = self._view(records, (
                _Point("r000", "r000", saved="anchor"), _edge("bottom"),
                *("idle" for _ in range(110)),
                _Point("r099", "r099-xxxxxxxx", len(records[-1]) - 1, button=32), begin,
                *(_Point("r099", "r099-xxxxxxxx", len(records[-1]) - 2 + i % 2,
                         button=32, saved="end") for i in range(6)),
                _edge("top"), *("idle" for _ in range(5)),
                _edge("bottom"), *("idle" for _ in range(6)),
                _Point("r099", "r099-xxxxxxxx", len(records[-1]) - 1,
                       button=32, saved="end"), _Mark("selected"), finish,
            ), session=session)
        finally:
            probe.stop()
        self.assertEqual(host.copied, [expected])
        self.assertIn(f"{len(expected)} characters", strip_ansi(host.marks["selected"]))
        self.assertLessEqual(host.projection_builds, 40)
        totals, boundaries = session.selection_cache_info()
        self.assertLessEqual(totals, len(records))
        self.assertLessEqual(boundaries, 4)
        session.bind(ViewerSpec("other", "snapshot", "a"), parse_jsonl(_source("fresh")))
        self.assertEqual(session.selection_cache_info(), (0, 0))

    def test_selection_summary_context_changes_preserve_exact_character_counts(self):
        snapshot = parse_jsonl(_source({"content": {"items": [0, 1]}, "hidden": "secret"}))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        session = _render.RenderSession()
        session.bind(spec, snapshot)
        simple = '{\n  "content": {\n    "items": [\n      0,\n      1\n    ]\n  }\n}'
        verbose = simple[:-2] + ',\n  "hidden": "secret"\n}'
        folded = '{\n  "content": {\n    "items": [...]\n  },\n  "hidden": "secret"\n}'
        modes = (
            (ViewState(), simple),
            (ViewState(mode=ViewMode.VERBOSE), verbose),
            (ViewState(mode=ViewMode.VERBOSE, folds=frozenset({FoldIdentity(0, ("content", "items"))})), folded),
            (ViewState(mode=ViewMode.VERBOSE, folds=frozenset({FoldIdentity(0, ("content", "items"))}),
                       search=SearchState("secret", find_matches(snapshot, "secret"), 0)), folded),
            (ViewState(), simple),
        )
        for state, expected in modes:
            with self.subTest(mode=state.mode, folds=state.folds, search=state.search is not None):
                projection = session.projection(snapshot.records[0], 0, spec, state)
                selection = TextSelection(SelectionPoint(0, 0, 0),
                                          SelectionPoint(0, len(projection.lines) - 1, 0),
                                          1, 1, (0,), 0, 0, moved=True)
                state = replace(state, selection=selection)
                self.assertEqual(_render.selection_text(snapshot, spec, state, session), expected)
                self.assertEqual(_render.selection_character_count(snapshot, spec, state, session), len(expected))


if __name__ == "__main__":
    unittest.main()
