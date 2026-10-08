"""Per-view projection reuse without changing observable navigation."""
from __future__ import annotations

from dataclasses import replace
import gc
import json
import unittest
from unittest import mock
import weakref

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer import _cell_layout, _render, engine
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import FoldIdentity, ViewMode, ViewState
from tests.support import FakeHost


def _source(*values):
    return ("".join(json.dumps(value, ensure_ascii=False) + "\n"
                    for value in values)).encode()


class _Navigation:
    """Keep the last painted result; never add a pre-event refresh."""

    def __init__(self, snapshot, spec, *, session=None, state=None, size=(96, 24)):
        self.snapshot = snapshot
        self.spec = spec
        self.session = session
        self.state = state or ViewState()
        self.size = size
        self.color = False
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
        with mock.patch.object(_render, "_format_record", wraps=_render._format_record) as formats:
            for index in (0, 1, 0, 2, 0, 1):
                observed.append(_Navigation(snapshot, spec, session=session,
                                            state=ViewState(selected_index=index), size=(40, 8)).frame.text)
            self.assertEqual(formats.call_count, 4)
        expected = [_Navigation(snapshot, spec, state=ViewState(selected_index=index),
                                size=(40, 8)).frame.text for index in (0, 1, 0, 2, 0, 1)]
        self.assertEqual(observed, expected)

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
