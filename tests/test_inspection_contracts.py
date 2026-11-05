"""Generic inspection descriptors contain bounded inert data, never policies."""

from __future__ import annotations

import dataclasses
import math
import unittest
from unittest import mock

import jsonl_viewer.inspection as inspection_module
from jsonl_viewer import ViewerSpec
from jsonl_viewer.inspection import (
    DECODE, MISSING, Context, DecodeJson, Facet, Group, Inspection, Option,
    SearchScope, Section, Target,
)
from jsonl_viewer.inspection import scalar_key


class InspectionContractTests(unittest.TestCase):
    def test_generic_spec_needs_no_identity_headers_and_keeps_legacy_calls(self):
        generic = ViewerSpec(context=(Context("Dataset", "audit"),))
        self.assertIsNone(generic.session_id)
        self.assertIsNone(generic.conversation_id)
        self.assertIsNone(generic.agent_id)
        self.assertEqual(generic.primary_fields, ())
        self.assertEqual(generic.context, (Context("Dataset", "audit"),))
        legacy = ViewerSpec("s", "c", "a")
        self.assertEqual(legacy.primary_fields, ("timestamp", "request_type", "content"))
        with self.assertRaises(ValueError):
            ViewerSpec("s")
        with self.assertRaises(ValueError):
            ViewerSpec(None, "c", "a")

    def test_all_descriptors_are_frozen_and_keep_explicit_data(self):
        section = Section("read", "Reads", (1, 3))
        group = Group("audit-a", "Audit A", (1, 3), sections=(section,),
                      values=(("severity", "warn"),), annotations=(Context("File", "a"),))
        facet = Facet("severity", "Severity", (Option("warn", "Warning"),))
        scope = SearchScope("message", "Message", (Target(1, ("body", DECODE, "message")),))
        inspection = Inspection((group,), (facet,), (scope,))
        self.assertEqual(inspection.groups[0].records, (1, 3))
        self.assertEqual(scope.targets[0].path, ("body", DecodeJson(), "message"))
        for value, field in ((section, "label"), (group, "label"), (facet, "label"),
                             (scope, "label"), (scope.targets[0], "record"),
                             (inspection, "groups"), (Option(None, "Null"), "value"),
                             (Context("Name", "A"), "value")):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(value, field, None)
                self.assertFalse(hasattr(value, "__dict__"))

    def test_missing_null_empty_boolean_and_numbers_have_distinct_keys(self):
        values = (MISSING, None, "", True, False, 1, 1.0, "1")
        keys = tuple(scalar_key(value) for value in values)
        self.assertEqual(len(set(keys)), len(values))
        facet = Facet("value", "Value", tuple(Option(v, str(i)) for i, v in enumerate(values)), path=())
        self.assertEqual(tuple(option.value for option in facet.options), values)
        with self.assertRaises(ValueError):
            Facet("value", "Value", (Option(1, "First"), Option(1, "Duplicate")))
        for bad in (math.nan, math.inf, -math.inf, {}, [], object()):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    Option(bad, "Invalid")

    def test_references_and_paths_are_data_not_expression_strings(self):
        literal = "payload.items[0].status /pointer $.*"
        self.assertEqual(Target(1, (literal,)).path, (literal,))
        self.assertEqual(Target(1, ("items", 0, DECODE)).record, 1)
        for record in (0, -1, True, "1", 1.0):
            with self.subTest(record=record):
                with self.assertRaises((TypeError, ValueError)):
                    Target(record)
        for path in (["value"], (True,), (-1,), (object(),), ("value",) * 65):
            with self.subTest(path_kind=type(path).__name__, length=len(path)):
                with self.assertRaises((TypeError, ValueError)):
                    Target(1, path)

    def test_mutable_collections_and_unknown_objects_are_rejected(self):
        bad_constructors = (
            lambda: ViewerSpec(context=[Context("Name", "A")]),
            lambda: ViewerSpec(inspection={"groups": []}),
            lambda: Inspection(groups=[]),
            lambda: Inspection(facets=(object(),)),
            lambda: Inspection(scopes=[SearchScope("m", "Message", ())]),
            lambda: Group("g", "Group", [1]),
            lambda: Group("g", "Group", (1,), annotations=[Context("Name", "A")]),
            lambda: Facet("f", "Facet", options=[Option("x", "X")]),
            lambda: Facet("f", "Facet", multiple=1),
            lambda: Section("s", "Section", records=[1]),
            lambda: SearchScope("s", "Scope", targets=[Target(1)]),
        )
        for create in bad_constructors:
            with self.subTest(create=create):
                with self.assertRaises((TypeError, ValueError)):
                    create()

    def test_callbacks_are_not_invoked_or_accepted_as_metadata(self):
        calls = []

        def callback():
            calls.append(True)
            raise AssertionError("Descriptors must never execute callbacks")

        for create in (
            lambda: Option(callback, "Bad"),
            lambda: Context("Bad", callback),
            lambda: Target(1, (callback,)),
            lambda: Inspection(groups=(callback,)),
            lambda: Group("g", "Group", (1,), values=(("f", callback),)),
        ):
            with self.subTest(create=create):
                with self.assertRaises((TypeError, ValueError)):
                    create()
        self.assertEqual(calls, [])

    def test_bounds_and_duplicate_identifiers_are_explicit(self):
        self.assertEqual(Context("Value", "a" * 2_048).value, "a" * 2_048)
        for create in (
            lambda: Context("x" * 513, "a"),
            lambda: Context("x", "a" * 2_049),
            lambda: Facet("f", "Facet", tuple(Option(i, str(i)) for i in range(257))),
            lambda: Inspection(facets=tuple(Facet(str(i), str(i)) for i in range(65))),
            lambda: Inspection(facets=(Facet("f", "One"), Facet("f", "Two"))),
            lambda: Inspection(groups=(Group("g", "One", (1,)), Group("g", "Two", (2,)))),
            lambda: Inspection(scopes=(SearchScope("s", "One", ()), SearchScope("s", "Two", ()))),
        ):
            with self.subTest(create=create):
                with self.assertRaises((TypeError, ValueError)):
                    create()

    def test_metadata_budget_short_circuits_without_rendering_whole_graph(self):
        shared = "v" * 400
        attributes = tuple((f"field-{index}", shared) for index in range(32))
        groups = tuple(Group(str(index), "Repeated attributes", (index + 1,), values=attributes)
                       for index in range(4))
        with mock.patch.object(inspection_module, "MAX_METADATA_BYTES", 2_048), mock.patch.object(
            Group, "__repr__", side_effect=AssertionError("Do not materialize the descriptor graph")
        ):
            with self.assertRaises(ValueError):
                Inspection(groups=groups)


if __name__ == "__main__":
    unittest.main()
