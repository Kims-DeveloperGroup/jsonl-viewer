"""Independent boundaries reported while reviewing generic group-list navigation."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._browser import _select, transition_browser
from jsonl_viewer._browser_render import render_browser
from jsonl_viewer._catalog import bind_catalog
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import BrowserState, CursorPosition
from jsonl_viewer._query import evaluate
from jsonl_viewer._render import RenderSession, _text_cells
from jsonl_viewer.inspection import Context, Group, Inspection, Section
from tests.inspection_support import Checkpoint, InteractiveHost, audit_records, jsonl
from tests.test_inspection_query import _audit_description
from tests.test_inspection_ui import _search, _severity_error


class GroupListReviewRegressionTests(unittest.TestCase):
    def _view(self, source, spec, events):
        digest = hashlib.sha256(source).digest()
        host = InteractiveHost((*events, "close"), size=(200, 32))
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        return host.checkpoints

    def _state(self, source, spec, *, focus="list"):
        snapshot = parse_jsonl(source)
        catalog = bind_catalog(snapshot, spec.inspection)
        filters = tuple(frozenset() for _ in catalog.facets)
        state = BrowserState(filters=filters, result=evaluate(snapshot, catalog, filters, None),
                             focus=focus)
        return snapshot, catalog, _select(state, catalog, 0)

    def test_detail_cross_item_routes_retain_the_departed_groups_latest_record(self):
        source = jsonl("A-first", "A-second needle", "B-only needle")
        spec = ViewerSpec(inspection=Inspection(groups=(Group("a", "Batch A", (1, 2)),
                             Group("b", "Sensor B", (3,)))), input_protocol="keys")
        for event in ("text\tj", "mouse\t65\t20\t8\tpress", "goto\t3", "next_match"):
            with self.subTest(route=event):
                prepare = ("search\tneedle",) if event == "next_match" else ("key\tdown",)
                frames = self._view(source, spec, (*prepare, Checkpoint("a"), event,
                    Checkpoint("b"), "key\ttab", "key\tup", "key\tenter", Checkpoint("returned")))
                self.assertIn("Record 2/2", frames["a"])
                self.assertIn("B-only", frames["b"])
                self.assertIn("Record 2/2", frames["returned"])
                self.assertIn("A-second", frames["returned"])

    def test_empty_configured_section_is_a_valid_restorable_group_location(self):
        source = jsonl("batch data", "sensor data")
        spec = ViewerSpec(inspection=Inspection(groups=(
            Group("batch", "Batch", (1,), sections=(
                Section("empty", "No samples", (), empty_text="No sensor samples"),
                Section("data", "Samples", (1,)))),
            Group("sensor", "Sensor", (2,)))), input_protocol="keys")
        frames = self._view(source, spec, ("text\t[", Checkpoint("empty"), "key\ttab",
            "key\tdown", "key\tenter", "key\ttab", "key\tup", "key\tenter", Checkpoint("returned")))
        self.assertIn("[No samples]", frames["empty"])
        self.assertIn("[No samples]", frames["returned"])
        self.assertIn("No sensor samples", frames["returned"])
        self.assertNotIn('"batch data"', frames["returned"])

    def test_main_escape_clears_name_find_before_body_query_without_changing_focus(self):
        source = jsonl(*audit_records())
        spec = ViewerSpec(context=(Context("Dataset", "Audit"),), inspection=_audit_description(),
                          input_protocol="keys")
        frames = self._view(source, spec, (*_severity_error(), *_search("timeout", messages=True),
            "key\ttab", "text\tt", "text\tTrace", "key\tenter", Checkpoint("detail"),
            "key\tescape", Checkpoint("detail_cleared"), "key\ttab", Checkpoint("list_cleared"),
            "text\tt", "text\tTrace", "key\tenter", "key\ttab", "key\tescape",
            Checkpoint("list_escape")))
        for name in ("detail_cleared", "list_cleared", "list_escape"):
            self.assertIn("Search: 'timeout'", frames[name])
            self.assertIn("In: Messages", frames[name])
            self.assertIn("Severity=Error", frames[name])
        self.assertIn("Focus: Detail", frames["detail_cleared"])
        self.assertIn("Focus: List", frames["list_escape"])
        self.assertIn("Name/ID: ''", frames["list_cleared"])
        self.assertIn("Name/ID: ''", frames["list_escape"])

    def test_three_digit_information_progress_never_clips_any_opaque_value_character(self):
        label = "".join(chr(0x4E00 + i % 128) for i in range(512))
        identity = "".join(chr(0x5000 + i) for i in range(128))
        source = jsonl("sensor")
        spec = ViewerSpec(inspection=Inspection(groups=(Group(identity, label, (1,)),)),
                          input_protocol="keys")
        snapshot, catalog, state = self._state(source, spec)
        session = RenderSession()
        chunks = {}
        last_frames = []
        largest_total = 0
        for _ in range(220):
            frame = render_browser(spec, state, snapshot, catalog, columns=24, rows=10,
                                   color=False, session=session)
            self.assertTrue(all(_text_cells(line) <= 24 for line in frame.text.splitlines()))
            lines = frame.text.splitlines()
            info = next(line for line in lines if line.startswith("Info "))
            _, progress, value = info.split(" ", 2)
            current, total = map(int, progress.split("/"))
            largest_total = max(largest_total, total)
            chunks[current] = value.rstrip()
            last_frames = (last_frames + [frame.text])[-2:]
            state, closed = transition_browser(state, "text\t.", snapshot, catalog, spec, frame, session)
            self.assertFalse(closed)
        self.assertGreater(largest_total, 99)
        self.assertEqual(len(chunks), largest_total)
        information = "".join(chunks[index] for index in sorted(chunks))
        self.assertIn(label, information)
        self.assertIn(identity, information)
        self.assertEqual(state.info_offset, frame.info_max_offset)
        self.assertEqual(last_frames[0], last_frames[1])

    def test_compact_list_reserves_a_real_item_row_before_optional_chrome(self):
        source = jsonl("batch", "sensor")
        spec = ViewerSpec(inspection=Inspection(groups=(Group("a", "Batch", (1,)),
                                                       Group("b", "Sensor", (2,)))),
                          input_protocol="keys")
        snapshot, catalog, state = self._state(source, spec)
        for rows in (6, 7, 8):
            with self.subTest(rows=rows):
                frame = render_browser(spec, state, snapshot, catalog, columns=64, rows=rows,
                                       color=False, session=RenderSession())
                hits = [hit for hit in frame.hits if hit.kind == "item"]
                self.assertGreaterEqual(len(hits), 1)
                self.assertEqual(len(frame.text.splitlines()), rows)
                for hit in hits:
                    self.assertLess(hit.row, rows - 2)
                    self.assertIn(catalog.items[hit.index].label, frame.text.splitlines()[hit.row])

    def test_hidden_list_does_not_revisit_other_groups_members_for_search_statistics(self):
        class CountedMembers(tuple):
            visits = 0

            def __iter__(self):
                self.visits += 1
                return super().__iter__()

        source = jsonl(*({"message": "needle needle needle", "row": index} for index in range(200)))
        description = Inspection(groups=(Group("batch", "Batch", (1,)),
            Group("sensors", "Sensors", tuple(range(2, 201)))))
        spec = ViewerSpec(inspection=description, input_protocol="keys")
        snapshot, catalog, state = self._state(source, spec, focus="detail")
        result = evaluate(snapshot, catalog, state.filters, "needle")
        state = _select(replace(state, result=result, query="needle"), catalog, 0, first_hit=True)
        members = CountedMembers(catalog.items[1].records)
        catalog = replace(catalog, items=(catalog.items[0], replace(catalog.items[1], records=members)))
        session = RenderSession()
        for cursor in (0, 1, 2):
            detail = replace(state.detail, cursor=CursorPosition(0, 0, cursor))
            frame = render_browser(spec, replace(state, detail=detail), snapshot, catalog,
                                   columns=180, rows=30, color=False, session=session)
            self.assertIn("needle", frame.text)
        self.assertEqual(members.visits, 0)


if __name__ == "__main__":
    unittest.main()
