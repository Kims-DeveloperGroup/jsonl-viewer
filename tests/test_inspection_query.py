"""Independent generic grouping, typed filtering, and source-faithful scopes."""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

import jsonl_viewer._catalog as catalog_module
from jsonl_viewer._catalog import bind_catalog
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._query import evaluate
from jsonl_viewer._search import SearchLimitError
from jsonl_viewer.inspection import (
    DECODE, MISSING, Context, Facet, Group, Inspection, Option, SearchScope, Section, Target,
)
from tests.inspection_support import audit_records, jsonl


def _selections(catalog, **values):
    """Select explicit typed values independently of the query implementation."""
    result = []
    for facet in catalog.facets:
        chosen = values.get(facet.id, ())
        result.append(frozenset(index for index, option in enumerate(facet.options)
                               if any(type(option.value) is type(value) and option.value == value
                                      for value in chosen)))
    return tuple(result)


def _audit_description() -> Inspection:
    sections = (
        Section("open", "Opened", (1,)), Section("read", "Read events", (3, 5)),
        Section("write", "Written", (7,)), Section("close", "Closed", (8,)),
    )
    return Inspection(
        groups=(
            Group("alpha", "Trace Alpha", (8, 1, 5, 3, 7), sections=sections,
                  values=(("severity", "error"), ("backend", "local")),
                  annotations=(Context("Note", "annotation-only-needle"),)),
            Group("beta", "Trace Beta", (4,), values=(("severity", "info"), ("backend", "remote"))),
        ),
        facets=(
            Facet("severity", "Severity", (Option("info", "Info"), Option("warn", "Warning"),
                                            Option("error", "Error"), Option(MISSING, "Missing"))),
            Facet("backend", "Backend", (Option("local", "Local"), Option("remote", "Remote"),
                                          Option(MISSING, "Missing")), multiple=False),
        ),
        scopes=(SearchScope("messages", "Messages", tuple(Target(i, ("message",)) for i in range(1, 9))),),
    )


class InspectionQueryTests(unittest.TestCase):
    def setUp(self):
        self.source = jsonl(*audit_records())
        self.snapshot = parse_jsonl(self.source)

    def test_arbitrary_sections_repeated_events_noncontiguous_members_and_orphans(self):
        catalog = bind_catalog(self.snapshot, _audit_description())
        self.assertEqual([item.records for item in catalog.items], [(0, 2, 4, 6, 7), (1,), (3,), (5,)])
        self.assertEqual(catalog.record_items, (0, 1, 0, 2, 0, 3, 0, 0))
        alpha = catalog.items[0]
        self.assertEqual([section.label for section in alpha.sections], ["Opened", "Read events", "Written", "Closed"])
        self.assertEqual(alpha.sections[1].records, (2, 4))
        self.assertTrue(alpha.grouped)
        self.assertFalse(catalog.items[1].grouped)
        self.assertFalse(catalog.items[3].grouped)
        self.assertEqual(len(catalog.items[2].sections), 1)

    def test_sections_do_not_hide_unassigned_members_and_keep_external_empty_text(self):
        description = Inspection(groups=(Group("g", "Group", (1, 3), sections=(
            Section("first", "First", (1,)), Section("empty", "Optional", (), "Nothing supplied"),
        )),))
        item = bind_catalog(self.snapshot, description).items[0]
        self.assertEqual(item.sections[1].records, ())
        self.assertEqual(item.sections[1].empty_text, "Nothing supplied")
        self.assertEqual(item.sections[-1].records, (2,))
        self.assertEqual({member for section in item.sections for member in section.records}, {0, 2})

    def test_filter_or_within_and_between_facets_and_text_search(self):
        catalog = bind_catalog(self.snapshot, _audit_description())
        selected = _selections(catalog, severity=("warn", "error"), backend=("local",))
        result = evaluate(self.snapshot, catalog, selected, "timeout", 0)
        self.assertEqual(result.filtered, (0,))
        self.assertEqual(result.items, (0,))
        self.assertEqual(result.matching_records, 2)
        hits = result.search.occurrences
        self.assertEqual([(hit.record_index, hit.path, hit.start, hit.end) for hit in hits], [
            (2, ("message",), 0, 7), (2, ("message",), 8, 15), (4, ("message",), 0, 7),
        ])
        self.assertEqual(catalog.items[result.items[0]].records, (0, 2, 4, 6, 7))
        any_severity = _selections(catalog, severity=("info", "error"))
        self.assertEqual(evaluate(self.snapshot, catalog, any_severity, None).items, (0, 2))

    def test_group_facets_do_not_aggregate_or_infer_member_values(self):
        description = Inspection(groups=(Group("g", "Explicit group", (1, 3, 5)),),
                                 facets=(Facet("level", "Level", path=("level",)),))
        catalog = bind_catalog(self.snapshot, description)
        self.assertIs(catalog.items[0].values[0], MISSING)
        selected = _selections(catalog, level=(MISSING,))
        self.assertIn(0, evaluate(self.snapshot, catalog, selected, None).items)

    def test_missing_null_empty_boolean_and_number_filter_independently(self):
        snapshot = parse_jsonl(jsonl({}, {"status": None}, {"status": ""}, {"status": True}, {"status": 1}))
        catalog = bind_catalog(snapshot, Inspection(facets=(Facet("status", "Status", path=("status",)),)))
        for value, expected in ((MISSING, 0), (None, 1), ("", 2), (True, 3), (1, 4)):
            with self.subTest(value=value, value_type=type(value).__name__):
                selected = _selections(catalog, status=(value,))
                self.assertEqual(evaluate(snapshot, catalog, selected, None).items, (expected,))

    def test_no_query_does_not_turn_search_scope_into_a_record_filter(self):
        catalog = bind_catalog(self.snapshot, _audit_description())
        result = evaluate(self.snapshot, catalog, _selections(catalog), None, 0)
        self.assertEqual(result.filtered, (0, 1, 2, 3))
        self.assertEqual(result.items, result.filtered)
        self.assertIsNone(result.search)
        zero = evaluate(self.snapshot, catalog, _selections(catalog), "absent", 0)
        self.assertEqual(zero.filtered, (0, 1, 2, 3))
        self.assertEqual(zero.items, ())
        self.assertEqual(zero.matching_records, 0)

    def test_labels_and_filter_values_can_change_without_altering_mechanisms(self):
        original = bind_catalog(self.snapshot, _audit_description())
        first = evaluate(self.snapshot, original, _selections(original, severity=("error",)), "timeout")
        renamed = Inspection(
            groups=(Group("unit-x", "Opaque bundle", (1, 3, 5, 7, 8), values=(("rank", "delta"),)),
                    Group("unit-y", "Different bundle", (4,), values=(("rank", "epsilon"),))),
            facets=(Facet("rank", "Arbitrary attribute", (Option("delta", "First choice"),
                                                          Option("epsilon", "Second choice"))),),
        )
        catalog = bind_catalog(self.snapshot, renamed)
        second = evaluate(self.snapshot, catalog, _selections(catalog, rank=("delta",)), "timeout")
        self.assertEqual(first.items, second.items)
        self.assertEqual(first.search.occurrences, second.search.occurrences)

    def test_source_and_all_search_do_not_include_synthetic_annotations(self):
        digest = hashlib.sha256(self.source).digest()
        plain = bind_catalog(self.snapshot, None)
        grouped = bind_catalog(self.snapshot, _audit_description())
        no_helper = evaluate(self.snapshot, grouped, _selections(grouped), "annotation-only-needle")
        self.assertEqual(no_helper.items, ())
        self.assertEqual(no_helper.search.occurrences, ())
        first = evaluate(self.snapshot, plain, _selections(plain), "timeout")
        second = evaluate(self.snapshot, grouped, _selections(grouped), "timeout")
        key = lambda hit: (hit.record_index, hit.path, hit.kind, hit.start, hit.end, hit.text)
        self.assertEqual([key(hit) for hit in first.search.occurrences],
                         [key(hit) for hit in second.search.occurrences])
        self.assertEqual(hashlib.sha256(self.source).digest(), digest)
        self.assertEqual([record.source_line for record in self.snapshot.records], list(range(1, 9)))

    def test_real_schema_key_and_explicit_decoded_scope_preserve_source_paths(self):
        encoded = json.dumps(json.dumps({"items": [{"message": "동기 timeout"}]}))
        snapshot = parse_jsonl(jsonl({"schema": "customer_account", "body": encoded}))
        target = Target(1, ("body", DECODE, "items", 0, "message"))
        description = Inspection(scopes=(
            SearchScope("schema", "Actual schema value", (Target(1, ("schema",)),)),
            SearchScope("decoded", "Decoded message", (target, target)),
        ))
        catalog = bind_catalog(snapshot, description)
        schema = evaluate(snapshot, catalog, _selections(catalog), "customer_account", 0)
        self.assertEqual(len(schema.search.occurrences), 1)
        self.assertEqual(schema.search.occurrences[0].path, ("schema",))
        decoded = evaluate(snapshot, catalog, _selections(catalog), "동기", 1)
        self.assertEqual(len(decoded.search.occurrences), 1)
        self.assertEqual(decoded.search.occurrences[0].record_index, 0)
        self.assertEqual(decoded.search.occurrences[0].path, ("body", "items", 0, "message"))
        self.assertEqual((decoded.search.occurrences[0].start, decoded.search.occurrences[0].end), (0, 2))

    def test_repeated_and_overlapping_targets_share_one_decoded_source_subtree(self):
        snapshot = parse_jsonl(jsonl({"body": json.dumps({"a": {"message": "timeout"}, "b": "done"})}))
        description = Inspection(scopes=(
            SearchScope("one", "First", (Target(1, ("body", DECODE, "a")),
                                          Target(1, ("body", DECODE, "a", "message")))),
            SearchScope("two", "Second", (Target(1, ("body", DECODE, "b")),)),
        ))
        with mock.patch.object(catalog_module, "expand_json_string", wraps=catalog_module.expand_json_string) as decode:
            catalog = bind_catalog(snapshot, description)
        self.assertEqual(decode.call_count, 1)
        self.assertEqual([scope.unavailable for scope in catalog.scopes], [(), ()])
        first = evaluate(snapshot, catalog, _selections(catalog), "timeout", 0)
        self.assertEqual(len(first.search.occurrences), 1)
        self.assertEqual(first.search.occurrences[0].path, ("body", "a", "message"))
        second = evaluate(snapshot, catalog, _selections(catalog), "done", 1)
        self.assertEqual(len(second.search.occurrences), 1)

    def test_unavailable_scope_is_distinct_and_filtered_to_eligible_records(self):
        scope = SearchScope("m", "Selected values", (Target(1, ("message",)), Target(6, ("absent",))),
                            unavailable=((3, "Part omitted by data owner"),))
        catalog = bind_catalog(self.snapshot, Inspection(facets=(Facet("level", "Level", path=("level",)),),
                                                       scopes=(scope,)))
        result = evaluate(self.snapshot, catalog, _selections(catalog), "never-present", 0)
        self.assertEqual(result.items, ())
        self.assertEqual({index for index, _reason in result.unavailable}, {2, 5})
        selected = _selections(catalog, level=("info",))
        narrowed = evaluate(self.snapshot, catalog, selected, "never-present", 0)
        self.assertEqual(narrowed.unavailable, ())

    def test_plain_roots_and_high_cardinality_heterogeneous_logs_remain_usable(self):
        roots = ({}, [], "needle", 27, True, None, {"nested": {"key": "needle"}})
        snapshot = parse_jsonl(jsonl(*roots))
        catalog = bind_catalog(snapshot, None)
        self.assertEqual(len(catalog.items), len(roots))
        self.assertEqual(evaluate(snapshot, catalog, _selections(catalog), "needle").items, (2, 6))
        records = [{"id": i, "message": f"entry-{i}", "level": "info", "mixed": {} if i % 2 else "value"}
                   for i in range(300)]
        snapshot = parse_jsonl(jsonl(*records))
        catalog = bind_catalog(snapshot, None)
        self.assertEqual(len(catalog.items), 300)
        self.assertNotIn("id", [facet.label for facet in catalog.facets])
        self.assertNotIn("mixed", [facet.label for facet in catalog.facets])
        result = evaluate(snapshot, catalog, _selections(catalog), "entry-299")
        self.assertEqual(result.items, (299,))

    def test_long_source_keys_and_values_cannot_make_automatic_facets_reject_data(self):
        long_key = "field-" + "k" * 600
        message = "prefix " + "x" * 4_096 + " late-needle"
        snapshot = parse_jsonl(jsonl({long_key: "value", "message": message}))
        catalog = bind_catalog(snapshot, None)
        result = evaluate(snapshot, catalog, _selections(catalog), "late-needle")
        self.assertEqual(result.items, (0,))
        self.assertEqual(result.search.occurrences[0].path, ("message",))
        self.assertEqual(snapshot.records[0].value[long_key], "value")

    def test_bad_references_duplicate_membership_and_section_references_reject(self):
        descriptions = (
            Inspection(groups=(Group("bad", "Bad", (99,)),)),
            Inspection(groups=(Group("a", "A", (1,)), Group("b", "B", (1,)))),
            Inspection(groups=(Group("a", "A", (1,), sections=(Section("outside", "Outside", (2,)),)),)),
            Inspection(scopes=(SearchScope("bad", "Bad", (Target(99),)),)),
            Inspection(scopes=(SearchScope("bad", "Bad", (), unavailable=((99, "Unavailable"),)),)),
        )
        for description in descriptions:
            with self.subTest(description=description):
                with self.assertRaises((TypeError, ValueError)):
                    bind_catalog(self.snapshot, description)

    def test_metadata_bound_and_explicit_scalar_type_fail_without_source_changes(self):
        digest = hashlib.sha256(self.source).digest()
        with self.assertRaises((TypeError, ValueError)):
            bind_catalog(self.snapshot, Inspection(facets=(Facet("f", "Container", path=()),)))
        with self.assertRaises((TypeError, ValueError)):
            large = Inspection(groups=tuple(Group(str(i), "x" * 512, (i + 1,)) for i in range(2100)))
            bind_catalog(parse_jsonl(jsonl(*(None for _ in range(2100)))), large)
        self.assertEqual(hashlib.sha256(self.source).digest(), digest)

    def test_invalid_filters_and_failed_dense_search_do_not_mutate_prior_result(self):
        snapshot = parse_jsonl(jsonl({"message": "x" * 100_001, "level": "info"}))
        catalog = bind_catalog(snapshot, Inspection(facets=(Facet("level", "Level", (Option("info", "Info"),),
                                                                        path=("level",), multiple=False),)))
        selected = _selections(catalog, level=("info",))
        prior = evaluate(snapshot, catalog, selected, "message")
        with self.assertRaises(SearchLimitError):
            evaluate(snapshot, catalog, selected, "x")
        with self.assertRaises(ValueError):
            evaluate(snapshot, catalog, (frozenset({99}),), "message")
        self.assertEqual(evaluate(snapshot, catalog, selected, "message"), prior)
        self.assertEqual(prior.items, (0,))


if __name__ == "__main__":
    unittest.main()
