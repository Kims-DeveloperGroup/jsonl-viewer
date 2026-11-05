"""Independent source-budget and public-input regressions from review #44."""

from __future__ import annotations

import hashlib
import unittest
from unittest import mock

from jsonl_viewer import ViewerSpec, view_jsonl
import jsonl_viewer._json as json_module
from jsonl_viewer._catalog import bind_catalog
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._query import evaluate
from jsonl_viewer.inspection import DECODE, Group, Inspection, SearchScope, Target
from tests.inspection_support import Checkpoint, InteractiveHost, jsonl
from tests.support import without_caret


def _catalog(snapshot, targets):
    return bind_catalog(snapshot, Inspection(scopes=(SearchScope("selected", "Selected values", targets),)))


def _search(snapshot, catalog, query):
    return evaluate(snapshot, catalog, tuple(frozenset() for _ in catalog.facets), query, 0)


def _identities(result):
    return frozenset((hit.record_index, hit.path, hit.kind, hit.start, hit.end, hit.text)
                     for hit in result.search.occurrences)


class ScopedExpansionReviewTests(unittest.TestCase):
    def assert_unavailable(self, result):
        self.assertEqual({index for index, _reason in result.unavailable}, {0})
        self.assertTrue(all(reason for _index, reason in result.unavailable))

    def test_disjoint_targets_share_original_record_node_and_byte_budgets(self):
        source = jsonl({"left": '["needle-a"]', "right": '["needle-b"]'})
        snapshot = parse_jsonl(source)
        targets = (Target(1, ("left",)), Target(1, ("right",)))
        for limit, value in (("MAX_DERIVED_VALUE_NODES", 3),
                             ("MAX_CUMULATIVE_DECODED_UTF8_BYTES", 15)):
            with self.subTest(limit=limit), mock.patch.object(json_module, limit, value):
                catalog = _catalog(snapshot, targets)
                result = _search(snapshot, catalog, "needle")
                hits = result.search.occurrences
                self.assertEqual(len(hits), 2)
                self.assertEqual(sum(len(hit.path) == 2 for hit in hits), 1)
                self.assertEqual(sum(len(hit.path) == 1 for hit in hits), 1)
                self.assertEqual({hit.path[0] for hit in hits}, {"left", "right"})
                self.assert_unavailable(result)
                for _ in range(2):
                    repeated = _search(snapshot, catalog, "needle")
                    self.assertEqual(repeated, result)
        self.assertEqual(source, jsonl(snapshot.records[0].value))
        separate = parse_jsonl(jsonl({"body": '["needle-a"]'}, {"body": '["needle-b"]'}))
        with mock.patch.object(json_module, "MAX_DERIVED_VALUE_NODES", 3):
            catalog = _catalog(separate, (Target(1, ("body",)), Target(2, ("body",))))
            result = _search(separate, catalog, "needle")
            self.assertEqual({hit.record_index for hit in result.search.occurrences}, {0, 1})
            self.assertTrue(all(len(hit.path) == 2 for hit in result.search.occurrences))
            self.assertEqual(result.unavailable, ())

    def test_deep_selected_root_keeps_original_display_depth(self):
        snapshot = parse_jsonl(jsonl({"nest": {"nest": {"body": '{"deep":{"message":"needle"}}'}}}))
        with mock.patch.object(json_module, "MAX_PROJECTED_DISPLAY_DEPTH", 4):
            catalog = _catalog(snapshot, (Target(1, ("nest", "nest", "body")),))
            result = _search(snapshot, catalog, "needle")
            self.assertEqual([hit.path for hit in result.search.occurrences], [("nest", "nest", "body")])
            self.assert_unavailable(result)
            self.assertEqual(_search(snapshot, catalog, "needle"), result)

    def test_explicit_decode_work_is_charged_before_searching_selected_descendants(self):
        source = jsonl({"body": '{"left":"[1]","right":"[2]"}'})
        snapshot = parse_jsonl(source)
        targets = (Target(1, ("body", DECODE, "left")), Target(1, ("body", DECODE, "right")))
        for limit, value in (("MAX_DERIVED_VALUE_NODES", 4),
                             ("MAX_CUMULATIVE_DECODED_UTF8_BYTES",
                              len(snapshot.records[0].value["body"].encode()) + 2)):
            with self.subTest(limit=limit), mock.patch.object(json_module, limit, value):
                catalog = _catalog(snapshot, targets)
                result = _search(snapshot, catalog, "1")
                self.assertEqual([hit.path for hit in result.search.occurrences], [("body", "left")])
                self.assertEqual(result.search.occurrences[0].text, "[1]")
                self.assert_unavailable(result)
                self.assertEqual(_search(snapshot, catalog, "1"), result)
        self.assertEqual(source, jsonl(snapshot.records[0].value))

    def test_raw_and_decoded_equal_paths_do_not_depend_on_target_order(self):
        source = jsonl({"body": '{"a": 1}'})
        digest = hashlib.sha256(source).digest()
        snapshot = parse_jsonl(source)
        raw, decoded = Target(1, ("body",)), Target(1, ("body", DECODE))
        expected = {(0, ("body",), "raw", 4, 6, '{"a": 1}')}
        for targets in ((decoded, raw), (raw, decoded)):
            with self.subTest(targets=targets):
                result = _search(snapshot, _catalog(snapshot, targets), ": ")
                self.assertEqual(_identities(result), expected)
                self.assertEqual(result.unavailable, ())
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        self.assertEqual(snapshot.records[0].source_line, 1)

    def test_disjoint_decoded_targets_and_raw_prefix_preserve_each_representation(self):
        snapshot = parse_jsonl(jsonl({"body": '{"left":"needle","right":"needle"}'}))
        targets = (Target(1, ("body", DECODE, "left")),
                   Target(1, ("body", DECODE, "right")), Target(1, ("body",)))
        expected = frozenset().union(*(_identities(_search(snapshot, _catalog(snapshot, (target,)), "needle"))
                                      for target in targets))
        self.assertTrue(expected)
        for ordered in (targets, tuple(reversed(targets))):
            with self.subTest(targets=ordered):
                self.assertEqual(_identities(_search(snapshot, _catalog(snapshot, ordered), "needle")), expected)


class BrowserInputReviewTests(unittest.TestCase):
    def _view(self, source, events, *, size=(100, 20), description=None):
        host = InteractiveHost((*events, "close"), size=size)
        try:
            view_jsonl(source, ViewerSpec(inspection=description or Inspection(), input_protocol="keys"), host)
        finally:
            self.assertEqual(host.close_calls, 1)
        return host

    def test_backward_alias_and_wheel_skip_excluded_records_in_the_backward_direction(self):
        source = jsonl(*({"message": "eligible" if i % 2 else "excluded", "source": i}
                         for i in range(1, 6)))
        controls = ("key\tpage_up", "key\tup", "text\tb", "mouse\t64\t15\t9\tpress")
        frames = {}
        for event in controls:
            with self.subTest(event=event):
                host = self._view(source, ("search\teligible", "goto\t3", Checkpoint("three"),
                                           event, Checkpoint("back")))
                self.assertIn("record:3", host.checkpoints["three"])
                frame = host.checkpoints["back"]
                frames[event] = frame
                self.assertIn("record:1", frame)
                self.assertIn('"source": 1', frame)
                self.assertNotIn("record:5", frame)
        self.assertEqual(without_caret(frames["text\tb"]), without_caret(frames["key\tpage_up"]))

    def test_physical_horizontal_keys_pan_detail_and_still_edit_search_drafts(self):
        payload = "left-window-" + "A" * 80 + "middle-window-" + "B" * 80 + "right-window"
        source = jsonl({"payload": payload, "target": "abcd"})
        host = self._view(source, ("key\ttab", "key\tdown", Checkpoint("initial"),
                                  *("key\tright" for _ in range(10)), Checkpoint("right"),
                                  *("key\tleft" for _ in range(10)), Checkpoint("left"),
                                  "text\t/", "text\tac", "key\tleft", "text\tb",
                                  "key\tright", "text\td", Checkpoint("draft"),
                                  "key\tenter", Checkpoint("searched")), size=(80, 20))
        self.assertIn("left-window-", host.checkpoints["initial"])
        self.assertNotIn("right-window", host.checkpoints["initial"])
        self.assertIn("right-window", host.checkpoints["right"])
        self.assertNotIn("left-window-", host.checkpoints["right"])
        self.assertIn("left-window-", host.checkpoints["left"])
        self.assertIn("abcd", host.checkpoints["draft"])
        self.assertIn("Search: 'abcd'", host.checkpoints["searched"])
        self.assertIn("1 occurrences", host.checkpoints["searched"])

    def test_input_diagnostic_is_visible_with_source_bound_profiles_at_compact_and_wide_sizes(self):
        description = Inspection(groups=(Group("g", "Related rows", (1,)),), scopes=(
            SearchScope("body", "Bodies", (Target(1, ("body",)),)),))
        cases = ((b'{"body":"PRIVATE",}\n', "malformed"),
                 (b'{"body":"PRIVATE"}\xff\n', "not strict UTF-8"),
                 (b'{"body":1,"body":2}\n', "duplicate field"),
                 (b'{}\n\n', "blank record"),
                 (b'{"body":NaN}\n', "invalid JSON value"))
        for width in (80, 180):
            for source, expected in cases:
                with self.subTest(width=width, expected=expected):
                    host = self._view(source, (Checkpoint("error"),), size=(width, 24),
                                      description=description)
                    frame = host.checkpoints["error"]
                    # Rejoin wrapping inside each painted pane, never across its divider.
                    columns = ([], [])
                    for row in frame.splitlines():
                        left, divider, right = row.partition("│")
                        columns[0].append(left.rstrip())
                        if divider:
                            columns[1].append(right.rstrip())
                    diagnostic = "".join(columns[0]).casefold()
                    self.assertIn("input error", diagnostic)
                    self.assertIn(expected.casefold(), diagnostic)
                    expected_line = 2 if source == b"{}\n\n" else 1
                    self.assertIn(f"source line {expected_line}", diagnostic)
                    self.assertNotIn("unknown", diagnostic)
                    self.assertIn("READ ONLY", frame)
                    self.assertNotIn("PRIVATE", frame)

    def test_valid_input_still_rejects_unknown_group_and_scope_references(self):
        for description in (Inspection(groups=(Group("g", "Unknown row", (2,)),)),
                            Inspection(scopes=(SearchScope("s", "Unknown row", (Target(2),)),))):
            with self.subTest(description=description), self.assertRaises(ValueError):
                self._view(jsonl({"body": "valid"}), (), description=description)
