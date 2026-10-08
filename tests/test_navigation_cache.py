"""Per-view projection reuse without changing observable navigation."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import gc
import json
import unittest
from unittest import mock
import weakref

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer import _cell_layout, _render, engine
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._mouse import MouseEvent, hit_test
from jsonl_viewer._model import FoldIdentity, ViewMode, ViewState
from tests.support import FakeHost


def _source(*values):
    return ("".join(json.dumps(value, ensure_ascii=False) + "\n"
                    for value in values)).encode()


class _Navigation:
    """Keep the last painted result; never add a pre-event refresh."""

    def __init__(self, snapshot, spec, *, session=None, state=None, size=(96, 24), color=False):
        self.snapshot = snapshot
        self.spec = spec
        self.session = session
        self.state = state or ViewState()
        self.size = size
        self.color = color
        self.frames = []
        self.render()

    def render(self):
        self.frame = _render.render_frame(
            self.spec, self.state, snapshot=self.snapshot, diagnostic=None,
            columns=self.size[0], rows=self.size[1], color=self.color,
            session=self.session,
        )
        self.frames.append(self.frame)
        self.state = replace(
            self.state, cursor=self.frame.cursor if self.frame.characters else self.state.cursor,
            record_line_offset=self.frame.record_line_offset,
            horizontal_offset=self.frame.horizontal_offset,
            reveal_cursor=False, focus_container=None,
        )
        return self.frame

    def step(self, event):
        self.state, closed = engine._transition(
            self.state, event, self.snapshot, self.spec,
            page_size=self.frame.body_rows,
            selected_line_count=self.frame.selected_line_count,
            rendered=self.frame, session=self.session,
        )
        if closed:
            raise AssertionError("navigation fixture unexpectedly closed")
        return self.render()


class NavigationCacheTests(unittest.TestCase):
    def test_multi_record_viewport_moves_reuse_fitting_projections(self):
        values = [{"content": "abcdefghij" * 20, "tail": index} for index in range(3)]
        snapshot = parse_jsonl(_source(*values))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        events = ("text\tj", "text\tl", "text\tj", "text\th", "text\tk", "text\tk")
        for mode in (ViewMode.SIMPLE, ViewMode.VERBOSE):
            for color in (False, True):
                with self.subTest(mode=mode, color=color):
                    state = ViewState(mode=mode)
                    with mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats:
                        view = _Navigation(snapshot, spec, session=_render.RenderSession(),
                                           state=state, size=(120, 57), color=color)
                        self.assertEqual({cell.position.record_index for cell in view.frame.characters},
                                         {0, 1, 2})
                        cold_formats = formats.call_count
                        actual = [view.step(event) for event in events]
                        self.assertEqual(formats.call_count, cold_formats)
                    reference = _Navigation(snapshot, spec, state=state, size=(120, 57), color=color)
                    expected = [reference.step(event) for event in events]
                    self.assertEqual([frame.text for frame in actual], [frame.text for frame in expected])
                    self.assertEqual([frame.idle_text for frame in actual], [frame.idle_text for frame in expected])
                    self.assertEqual([frame.characters for frame in actual], [frame.characters for frame in expected])
                    self.assertEqual(view.state, reference.state)

    def test_dense_candidates_keep_full_mouse_cells_and_exact_blink_frames(self):
        snapshot = parse_jsonl(_source({"content": {
            f"row_{index:02}": "ABCD" * 80 for index in range(70)
        }}))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        for color in (False, True):
            with self.subTest(color=color):
                view = _Navigation(snapshot, spec, session=_render.RenderSession(),
                                   state=ViewState(mode=ViewMode.VERBOSE), size=(40, 57), color=color)
                narrow_candidates = Counter((cell.position.record_index, cell.position.line_index)
                                            for cell in view.frame.logical_characters)
                narrow_cells = len(view.frame.characters)
                view.size = (120, 57)
                frame = view.render()
                # Widening these same rows adds painted cells, not navigation needs.
                self.assertGreater(len(frame.characters), narrow_cells * 2)
                wide_candidates = Counter((cell.position.record_index, cell.position.line_index)
                                          for cell in frame.logical_characters)
                # Chrome can leave an extra body row at the wider geometry.
                for row in narrow_candidates.keys() & wide_candidates.keys():
                    self.assertEqual(wide_candidates[row], narrow_candidates[row])
                rows = _render.strip_ansi(frame.text).splitlines()
                for screen_row, line in enumerate(rows):
                    if " │ " not in line:
                        continue
                    expected = line.split(" │ ", 1)[1].lstrip().removesuffix("…")
                    cells = [cell for cell in frame.characters if cell.screen_row == screen_row]
                    self.assertEqual("".join(cell.text for cell in cells), expected)
                    # Include interior cells that are not sparse navigation anchors.
                    for cell in cells[::max(1, len(cells) // 3)]:
                        self.assertEqual(hit_test(MouseEvent(0, cell.screen_column + 1,
                                                             cell.screen_row + 1, "press"), frame), cell)
                fresh = _Navigation(snapshot, spec, state=view.state, size=view.size, color=color)
                self.assertEqual((frame.text, frame.idle_text, frame.characters),
                                 (fresh.frame.text, fresh.frame.idle_text, fresh.frame.characters))
                self.assertEqual(view.step("idle").text, frame.text)
                self.assertNotEqual(frame.text, frame.idle_text)

    def test_sparse_navigation_keeps_row_ends_preferred_columns_and_field_groups(self):
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        leaf = "A界e\u0301🙂Z" + "x" * 160
        for color in (False, True):
            with self.subTest(color=color, behavior="offscreen row ends"):
                view = _Navigation(parse_jsonl(_source(leaf)), spec,
                                   session=_render.RenderSession(), size=(40, 12), color=color)
                opening = view.frame.cursor
                closing = view.step("text\th")
                self.assertEqual(closing.cursor.column, opening.column + 168)
                self.assertGreater(closing.horizontal_offset, 0)
                self.assertEqual(self._focused(closing).text, '"')
                self.assertEqual(self._focused(view.step("text\th")).text, "x")
                self.assertEqual(view.step("text\tl").cursor, closing.cursor)
                self.assertEqual(view.step("text\tl").cursor, opening)
            with self.subTest(color=color, behavior="preferred column and siblings"):
                snapshot = parse_jsonl(_source({"content": {
                    "first": "A界e\u0301🙂B" + "x" * 120,
                    "second": "C界e\u0301🙂D", "last": 1,
                }}))
                view = _Navigation(snapshot, spec, session=_render.RenderSession(),
                                   state=ViewState(mode=ViewMode.VERBOSE), size=(40, 12), color=color)
                face = next(cell for cell in view.frame.characters if cell.text == "🙂")
                clicked = view.step(f"mouse\t0\t{face.screen_column + 2}\t{face.screen_row + 1}\tpress")
                self.assertEqual(clicked.cursor, face.position)
                self.assertEqual(self._focused(view.step("text\tj")).text, "e\u0301")
                self.assertEqual(self._focused(view.step("text\tk")).text, "🙂")
                view.step("scroll_right")
                self.assertEqual(self._focused(view.step("next_sibling")).text, "s")
                self.assertEqual(self._focused(view.step("text\tl")).text, "e")
                self.assertEqual(self._focused(view.step("previous_sibling")).text, "f")
                self.assertEqual(self._focused(view.step("text\tl")).text, "i")

    @staticmethod
    def _focused(frame):
        return next(cell for cell in frame.characters if cell.position == frame.cursor)

    def test_nested_containers_are_unique_and_focus_their_opening_rows(self):
        snapshot = parse_jsonl(_source(
            {"branch": [{"leaf": [1]}, {}], "tail": []},
        ))
        spec = ViewerSpec("s", "c", "a", title="Container opening rows")
        # Rows follow the actual verbose opening structure, including empty pairs.
        openings = (
            ((), 0, "{", True),
            (("branch",), 1, "[", True),
            (("branch", 0), 2, "{", True),
            (("branch", 0, "leaf"), 3, "[", True),
            (("branch", 1), 7, "{", False),
            (("tail",), 9, "[", False),
        )
        expected = [(FoldIdentity(0, path), nonempty, False)
                    for path, _, _, nonempty in openings]
        for cached in (False, True):
            session = _render.RenderSession() if cached else None
            for path, row, glyph, _ in openings:
                with self.subTest(cached=cached, path=path):
                    view = _Navigation(
                        snapshot, spec, session=session,
                        state=ViewState(mode=ViewMode.VERBOSE,
                                        focus_container=FoldIdentity(0, path)),
                    )
                    self.assertEqual(
                        [(item.identity, item.nonempty, item.folded)
                         for item in view.frame.containers], expected,
                    )
                    focused = next(cell for cell in view.frame.characters
                                   if cell.position == view.frame.cursor)
                    self.assertEqual(focused.position.line_index, row)
                    self.assertEqual((focused.text, focused.delimiter), (glyph, "open"))
                    self.assertEqual(focused.container, FoldIdentity(0, path))
            with self.subTest(cached=cached, folded=True):
                view = _Navigation(
                    snapshot, spec, session=session,
                    state=ViewState(mode=ViewMode.VERBOSE,
                                    focus_container=FoldIdentity(0, ("branch",))),
                )
                opening = view.frame.cursor
                folded = view.step("toggle_fold")
                self.assertEqual(folded.cursor, opening)
                self.assertEqual(
                    [(item.identity, item.nonempty, item.folded)
                     for item in folded.containers],
                    [(FoldIdentity(0, ()), True, False),
                     (FoldIdentity(0, ("branch",)), True, True),
                     (FoldIdentity(0, ("tail",)), False, False)],
                )
                expanded = view.step("toggle_fold")
                self.assertEqual(expanded.cursor, opening)
                self.assertEqual(
                    [(item.identity, item.nonempty, item.folded)
                     for item in expanded.containers], expected,
                )

    def test_warm_unicode_moves_reuse_projection_without_full_leaf_cell_scans(self):
        payload = "界e\u0301🙂" * 6553
        snapshot = parse_jsonl(_source({"content": payload}))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        state = ViewState(mode=ViewMode.VERBOSE)
        events = ("text\tl", "text\tl", "text\th")
        with mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats:
            cached = _Navigation(snapshot, spec, session=_render.RenderSession(), state=state)
            cold_formats = formats.call_count
            with mock.patch.object(_cell_layout, "character_cells",
                                   wraps=_cell_layout.character_cells) as widths:
                actual = [cached.step(event) for event in events]
            self.assertEqual(formats.call_count, cold_formats)
            # Three warm moves must not even traverse this whole retained leaf once.
            self.assertLess(widths.call_count, len(payload))
        reference = _Navigation(snapshot, spec, state=state)
        expected = [reference.step(event) for event in events]
        self.assertEqual([f.text for f in actual], [f.text for f in expected])
        self.assertEqual(cached.state, reference.state)
        self.assertNotEqual(actual[0].cursor, actual[1].cursor)
        self.assertEqual(actual[0].cursor, actual[2].cursor)

    def test_snapshot_and_primary_field_rebind_cannot_reuse_old_display(self):
        session = _render.RenderSession()
        spec = ViewerSpec("s", "c", "a")
        first = parse_jsonl(_source({"content": "OLD_CAPTURE"}))
        second = parse_jsonl(_source({"content": "NEW_CAPTURE", "message": "NEW_PRIMARY"}))
        _Navigation(first, spec, session=session)
        fresh = _Navigation(second, spec)
        rebound = _Navigation(second, spec, session=session)
        self.assertEqual(rebound.frame.text, fresh.frame.text)
        self.assertIn("NEW_CAPTURE", rebound.frame.text)
        self.assertNotIn("OLD_CAPTURE", rebound.frame.text)
        alternate = ViewerSpec("s", "c", "a", content_field="message")
        rebound = _Navigation(second, alternate, session=session)
        fresh = _Navigation(second, alternate)
        self.assertEqual(rebound.frame.text, fresh.frame.text)
        self.assertIn("NEW_PRIMARY", rebound.frame.text)

    def test_record_eviction_and_budget_fallback_preserve_exact_frames(self):
        values = [{"content": {"label": str(index), "rows": list(range(12))}}
                  for index in range(3)]
        snapshot = parse_jsonl(_source(*values))
        spec = ViewerSpec("s", "c", "a")
        session = _render.RenderSession()
        observed = []
        with mock.patch.object(_render, "_CACHE_RECORDS", 2), \
                mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats:
            for index in (0, 1, 0, 2, 0, 1):
                observed.append(_Navigation(snapshot, spec, session=session,
                                            state=ViewState(selected_index=index), size=(40, 8)).frame.text)
            self.assertEqual(formats.call_count, 4)
        expected = [_Navigation(snapshot, spec, state=ViewState(selected_index=index),
                                size=(40, 8)).frame.text for index in (0, 1, 0, 2, 0, 1)]
        self.assertEqual(observed, expected)

        # Individually fitting records must evict when their aggregate exceeds a cap.
        reference = _Navigation(snapshot, spec, session=_render.RenderSession(), size=(40, 8))
        totals = reference.session.cache_info()
        for name, metric, cap in (("_CACHE_RECORDS", 0, 1),
                                  ("_CACHE_TEXT_BYTES", 1, totals[1]),
                                  ("_CACHE_ROWS", 2, totals[2]),
                                  ("_CACHE_SEGMENTS", 3, totals[3])):
            with self.subTest(aggregate=name), mock.patch.object(_render, name, cap):
                bounded = _render.RenderSession()
                for index, expected_frame in zip((0, 1, 0, 2, 0, 1), expected):
                    view = _Navigation(snapshot, spec, session=bounded,
                                       state=ViewState(selected_index=index), size=(40, 8))
                    self.assertEqual(view.frame.text, expected_frame)
                    self.assertLessEqual(bounded.cache_info()[metric], cap)
                    self.assertEqual(bounded.cache_info()[0], 1)

        one = parse_jsonl(_source({"content": {"a": "界e\u0301🙂" * 20, "b": [1, 2, 3]}}))
        expected = _Navigation(one, spec, state=ViewState(mode=ViewMode.VERBOSE)).frame.text
        for limit in ("_CACHE_TEXT_BYTES", "_CACHE_ROWS", "_CACHE_SEGMENTS"):
            with self.subTest(budget=limit), mock.patch.object(_render, limit, 1):
                bounded = _render.RenderSession()
                navigation = _Navigation(one, spec, session=bounded,
                                         state=ViewState(mode=ViewMode.VERBOSE))
                self.assertEqual(navigation.frame.text, expected)
                self.assertEqual(bounded.cache_info(), (0, 0, 0, 0, 0))
        with mock.patch.object(_render, "_CACHE_CHECKPOINTS", 0):
            bounded = _render.RenderSession()
            navigation = _Navigation(one, spec, session=bounded,
                                     state=ViewState(mode=ViewMode.VERBOSE))
            self.assertEqual(navigation.frame.text, expected)
            self.assertEqual(bounded.cache_info()[-1], 0)

    def test_cached_frames_stay_fresh_across_matches_folds_modes_and_geometry(self):
        snapshot = parse_jsonl(_source(
            {"content": {"first": "needle 界e\u0301🙂", "second": "end needle",
                         "items": list(range(12))}},
            {"content": "other needle"},
        ))
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        cached = _Navigation(snapshot, spec, session=_render.RenderSession())
        fresh = _Navigation(snapshot, spec)

        def same():
            self.assertEqual(cached.frame.text, fresh.frame.text)
            self.assertEqual(cached.state, fresh.state)
            self.assertEqual(cached.frame.characters, fresh.frame.characters)

        same()
        for event in ("toggle_mode", "search\tneedle", "next_match", "scroll_right",
                      "text\tl", "scroll_left", "clear_search", "page_down", "page_up"):
            with self.subTest(event=event):
                cached.step(event)
                fresh.step(event)
                same()
        for folds in (frozenset({FoldIdentity(0, ("content", "items"))}), frozenset()):
            cached.state = replace(cached.state, folds=folds)
            fresh.state = replace(fresh.state, folds=folds)
            cached.render()
            fresh.render()
            same()
        for size, color in (((40, 12), False), ((40, 12), True), ((96, 24), False)):
            cached.size = fresh.size = size
            cached.color = fresh.color = color
            cached.render()
            fresh.render()
            same()
        cached.step("toggle_mode")
        fresh.step("toggle_mode")
        same()

    def test_public_views_release_sessions_and_never_reuse_previous_capture(self):
        created = []
        real_session = engine.RenderSession

        def session():
            value = real_session()
            created.append(weakref.ref(value))
            return value

        first_source = _source({"content": "FIRST_CAPTURE " + "x" * 8192})
        second_source = _source({"content": "SECOND_CAPTURE " + "y" * 8192})
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        first = FakeHost(("toggle_mode", "text\tl", "close"), size=(40, 12))
        second = FakeHost(("toggle_mode", "text\tl", "close"), size=(40, 12))
        with mock.patch.object(engine, "RenderSession", side_effect=session):
            view_jsonl(first_source, spec, first)
            gc.collect()
            self.assertIsNone(created[0]())
            view_jsonl(second_source, spec, second)
            gc.collect()
        self.assertEqual(len(created), 2)
        self.assertTrue(all(reference() is None for reference in created))
        self.assertIn("SECOND_CAPTURE", second.frames[-1])
        self.assertNotIn("FIRST_CAPTURE", second.frames[-1])
        self.assertEqual((first.close_calls, second.close_calls), (1, 1))
        self.assertEqual(first_source, _source({"content": "FIRST_CAPTURE " + "x" * 8192}))
        self.assertEqual(second_source, _source({"content": "SECOND_CAPTURE " + "y" * 8192}))


if __name__ == "__main__":
    unittest.main()
