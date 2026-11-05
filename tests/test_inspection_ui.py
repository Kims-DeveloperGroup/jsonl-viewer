"""Exercise generic inspection exclusively through public input and frames."""

from __future__ import annotations

import hashlib
from dataclasses import replace
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._render import _text_cells, strip_ansi
from jsonl_viewer.inspection import Context, Facet, Group, Inspection, Option, SearchScope, Section, Target
from tests.inspection_support import Checkpoint, InteractiveHost, PaintedClick, audit_records, jsonl
from tests.support import without_caret
from tests.test_inspection_query import _audit_description


def _key(name):
    return "key\t" + name


def _text(value):
    return "text\t" + value


def _search(query, *, messages=False):
    events = [_text("/"), _text(query)]
    if messages:
        events.extend((_key("tab"), _key("right"), _key("tab")))
    events.append(_key("enter"))
    return tuple(events)


def _severity_error():
    return (_text("f"), _key("enter"), *(_key("down") for _ in range(3)),
            _key("space"), _key("enter"), PaintedClick("[Apply]", "[Apply]"))


class InspectionUiTests(unittest.TestCase):
    def _view(self, events, *, source=None, spec=None, size=(200, 32), color=False):
        source = jsonl(*audit_records()) if source is None else source
        spec = spec or ViewerSpec(context=(Context("Dataset", "Audit"),),
                                  inspection=_audit_description(), input_protocol="keys")
        digest = hashlib.sha256(source).digest()
        host = InteractiveHost((*events, "close"), size=size, color=color)
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        return host

    def test_scope_counts_occurrence_navigation_and_arbitrary_sections(self):
        host = self._view((*_search("timeout", messages=True), Checkpoint("first"),
                           _text("n"), Checkpoint("second"), _text("n"), Checkpoint("third"),
                           _text("]"), Checkpoint("write"), _text("]"), Checkpoint("close"),
                           _text("]"), Checkpoint("open")))
        first = host.checkpoints["first"]
        self.assertIn("4 total · 4 filtered · 1 matched · 2 records · 3 occurrences", first)
        self.assertIn("In: Messages", first)
        self.assertIn("Read events", first)
        self.assertIn("1/3", first)
        self.assertIn("2/3", host.checkpoints["second"])
        self.assertIn("3/3", host.checkpoints["third"])
        self.assertIn('"message": "saved"', host.checkpoints["write"])
        self.assertIn('"message": "done"', host.checkpoints["close"])
        self.assertIn("연결 시작", host.checkpoints["open"])
        for name in ("write", "close", "open"):
            self.assertIn("1 matched · 2 records · 3 occurrences", host.checkpoints[name])

    def test_field_confirmation_is_only_a_draft_and_nested_escape_rolls_back(self):
        host = self._view((Checkpoint("before"), _text("f"), _key("enter"),
                           *(_key("down") for _ in range(3)), _key("space"), _key("enter"),
                           Checkpoint("draft"), _key("enter"), PaintedClick("All", "All"),
                           _key("escape"), Checkpoint("field_cancelled"),
                           _key("escape"), Checkpoint("panel_cancelled")))
        self.assertIn("Severity: [Error]", host.checkpoints["draft"])
        self.assertIn("Severity: [Error]", host.checkpoints["field_cancelled"])
        self.assertEqual(without_caret(host.checkpoints["before"]),
                         without_caret(host.checkpoints["panel_cancelled"]))

    def test_filter_or_and_single_select_replacement_apply_atomically(self):
        host = self._view((_text("f"), _key("enter"), _key("down"), _key("space"),
                           _key("down"), _key("down"), _key("space"), _key("enter"),
                           PaintedClick("[Apply]", "[Apply]"), Checkpoint("or"),
                           _text("f"), _key("down"), _key("enter"),
                           PaintedClick("Local", "Local"), PaintedClick("Remote", "Remote"),
                           Checkpoint("single"), _key("enter"), PaintedClick("[Apply]", "[Apply]"),
                           Checkpoint("and"), _text("f"), _key("down"), _key("enter"),
                           PaintedClick("Remote", "Remote"), _key("enter"),
                           PaintedClick("[Apply]", "[Apply]"), Checkpoint("last_deselected")))
        self.assertIn("4 total · 2 filtered", host.checkpoints["or"])
        self.assertIn("[x] Remote", host.checkpoints["single"])
        self.assertIn("[ ] Local", host.checkpoints["single"])
        self.assertIn("4 total · 1 filtered", host.checkpoints["and"])
        self.assertIn('"trace": "beta"', host.checkpoints["and"])
        self.assertIn("4 total · 2 filtered", host.checkpoints["last_deselected"])
        self.assertIn("Filters(1)", host.checkpoints["last_deselected"])

    def test_clear_reset_filters_and_reset_all_have_separate_commit_boundaries(self):
        host = self._view((*_search("timeout", messages=True), *_severity_error(), Checkpoint("filtered"),
                           _text("f"), PaintedClick("[Reset filters]", "[Reset filters]"),
                           Checkpoint("reset_draft"), _key("escape"), Checkpoint("cancelled"),
                           _text("f"), PaintedClick("[Reset filters]", "[Reset filters]"),
                           PaintedClick("[Apply]", "[Apply]"), Checkpoint("reset_filters"),
                           _text("f"), PaintedClick("[Reset all]", "[Reset all]"),
                           Checkpoint("all_draft"), _key("escape"), Checkpoint("all_cancelled"),
                           _text("f"), PaintedClick("[Reset all]", "[Reset all]"),
                           PaintedClick("[Apply]", "[Apply]"), Checkpoint("all_applied")))
        filtered = host.checkpoints["filtered"]
        self.assertIn("Filters(1)", filtered)
        self.assertIn("timeout", host.checkpoints["reset_draft"])
        self.assertIn("Messages", host.checkpoints["reset_draft"])
        self.assertEqual(without_caret(filtered), without_caret(host.checkpoints["cancelled"]))
        reset = host.checkpoints["reset_filters"]
        self.assertIn("Filters(0)", reset)
        self.assertIn("In: Messages", reset)
        self.assertIn("1 matched · 2 records · 3 occurrences", reset)
        self.assertIn("will clear on Apply", host.checkpoints["all_draft"])
        self.assertEqual(without_caret(reset), without_caret(host.checkpoints["all_cancelled"]))
        self.assertIn("4 total · 4 filtered", host.checkpoints["all_applied"])
        self.assertIn("In: All", host.checkpoints["all_applied"])
        self.assertNotIn(" matched", host.checkpoints["all_applied"])
        cleared = self._view((*_search("timeout", messages=True), *_severity_error(),
                              _text("c"), Checkpoint("cleared"))).checkpoints["cleared"]
        self.assertIn("Filters(1)", cleared)
        self.assertIn("In: Messages", cleared)
        self.assertIn("4 total · 1 filtered", cleared)
        self.assertNotIn(" matched", cleared)

    def test_zero_filter_zero_text_empty_source_and_partial_coverage_are_distinct(self):
        no_filter = self._view((_text("f"), _key("enter"), _key("down"), _key("down"),
                                _key("space"), _key("enter"), PaintedClick("[Apply]", "[Apply]"),
                                Checkpoint("zero"))).checkpoints["zero"]
        self.assertIn("No items match", no_filter)
        self.assertIn("filters", no_filter)
        self.assertNotIn('"trace": "alpha"', no_filter)
        no_text = self._view((*_search("never-present"), Checkpoint("zero"))).checkpoints["zero"]
        self.assertIn("No text matches", no_text)
        self.assertIn("4 filtered · 0 matched", no_text)
        empty = self._view((Checkpoint("empty"),), source=b"", spec=ViewerSpec(input_protocol="keys"))
        self.assertIn("No recorded data", empty.checkpoints["empty"])
        profile = Inspection(scopes=(SearchScope("partial", "Partial", (Target(1, ("absent",)),),
                                                unavailable=((2, "Only retained prefix available"),)),))
        spec = ViewerSpec(inspection=profile, input_protocol="keys")
        partial = self._view((*_search("never-present", messages=True), Checkpoint("partial")), spec=spec)
        self.assertIn("Search coverage incomplete: 2", partial.checkpoints["partial"])

    def test_search_draft_letters_unicode_and_cancel_preserve_committed_view(self):
        host = self._view((*_search("timeout", messages=True), Checkpoint("before"),
                           _text("/"), _key("ctrl_u"), _text("다른 문구fqn"), Checkpoint("draft"),
                           _key("escape"), Checkpoint("after")))
        self.assertIn("다른 문구fqn", host.checkpoints["draft"])
        self.assertIn("Search — literal text", host.checkpoints["draft"])
        self.assertEqual(without_caret(host.checkpoints["before"]),
                         without_caret(host.checkpoints["after"]))

    def test_failed_dense_search_preserves_previous_query_and_position(self):
        source = jsonl({"level": "info", "message": "x" * 100_001})
        spec = ViewerSpec(inspection=Inspection(facets=(Facet("level", "Level", (Option("info", "Info"),),
                                                                     path=("level",)),)), input_protocol="keys")
        host = self._view((*_search("message"), Checkpoint("before"), _text("/"), _key("ctrl_u"),
                           _text("x"), _key("enter"), Checkpoint("error"), _key("escape"),
                           Checkpoint("after")), source=source, spec=spec)
        self.assertIn("100000 occurrences", host.checkpoints["error"])
        self.assertIn("refine", host.checkpoints["error"])
        self.assertEqual(without_caret(host.checkpoints["before"]),
                         without_caret(host.checkpoints["after"]))

    def test_keyboard_and_painted_mouse_filters_have_identical_results(self):
        keyboard = self._view((*_severity_error(), Checkpoint("applied")))
        mouse = self._view((_text("f"), PaintedClick("Severity:", "Severity"),
                            Checkpoint("before_outside"), "mouse\t0\t1\t1\tpress", Checkpoint("after_outside"),
                            PaintedClick("Error", "Error"), _key("enter"),
                            PaintedClick("[Apply]", "[Apply]"), Checkpoint("applied")))
        self.assertEqual(mouse.checkpoints["before_outside"], mouse.checkpoints["after_outside"])
        self.assertEqual(without_caret(keyboard.checkpoints["applied"]),
                         without_caret(mouse.checkpoints["applied"]))

    def test_resize_preserves_draft_and_compact_focus_navigation(self):
        host = self._view((_text("f"), _key("enter"), *(_key("down") for _ in range(3)),
                           _key("space"), _key("enter"), Checkpoint("wide_draft"), (48, 14),
                           Checkpoint("narrow_draft"), _key("escape"), Checkpoint("list"),
                           _key("tab"), Checkpoint("detail"), _key("shift_tab"), Checkpoint("list_again")))
        self.assertIn("Severity: [Error]", host.checkpoints["wide_draft"])
        self.assertIn("Severity: [Error]", host.checkpoints["narrow_draft"])
        self.assertIn("Focus: List", host.checkpoints["list"])
        self.assertIn("Focus: Detail", host.checkpoints["detail"])
        self.assertIn('"trace": "alpha"', host.checkpoints["detail"])
        self.assertIn("Trace Alpha", host.checkpoints["list_again"])
        for name in ("narrow_draft", "list", "detail", "list_again"):
            self.assertTrue(all(_text_cells(line) <= 48 for line in host.checkpoints[name].splitlines()))

    def test_plain_and_ansi_frames_keep_equivalent_counts_and_readable_focus(self):
        for color in (False, True):
            with self.subTest(color=color):
                host = self._view((*_search("동기"), Checkpoint("matched")), color=color)
                frame = strip_ansi(host.checkpoints["matched"])
                self.assertIn("1 matched · 1 records · 1 occurrences", frame)
                self.assertIn("동기", frame)
                self.assertIn("Focus:", frame)
                self.assertTrue(all(_text_cells(line) <= 200 for line in frame.splitlines()))
                if not color:
                    self.assertNotIn("\x1b", host.checkpoints["matched"])

    def test_occurrence_position_survives_compact_plain_navigation(self):
        host = self._view((*_search("timeout", messages=True), (48, 14), Checkpoint("first"),
                           _text("n"), Checkpoint("second"), _text("N"), Checkpoint("back")))
        self.assertIn("Occurrence 1/3", host.checkpoints["first"])
        self.assertIn("Occurrence 2/3", host.checkpoints["second"])
        self.assertIn("Occurrence 1/3", host.checkpoints["back"])
        for name in ("first", "second", "back"):
            self.assertTrue(all(_text_cells(line) <= 48 for line in host.checkpoints[name].splitlines()))

    def test_full_annotation_is_reachable_without_becoming_searchable_source(self):
        description = _audit_description()
        first = replace(description.groups[0], annotations=(Context("Long note", "x" * 400 + " tail-here"),))
        description = replace(description, groups=(first, *description.groups[1:]))
        spec = ViewerSpec(inspection=description, input_protocol="keys")
        host = self._view((Checkpoint("before"), _text("a"), *(_key("down") for _ in range(15)),
                           Checkpoint("annotation"), _key("escape"), Checkpoint("after"),
                           *_search("tail-here"), Checkpoint("search")), spec=spec, size=(48, 14))
        wrapped_annotation = "".join(line.strip() for line in host.checkpoints["annotation"].splitlines()[3:-1])
        self.assertIn("tail-here", wrapped_annotation)
        self.assertIn("Item annotations", host.checkpoints["annotation"])
        self.assertEqual(without_caret(host.checkpoints["before"]),
                         without_caret(host.checkpoints["after"]))
        self.assertIn("No text matches", host.checkpoints["search"])

    def test_legacy_headers_with_explicit_inspection_use_the_new_browser_and_reveal_hits(self):
        source = jsonl({"content": "body", "outer_field": "late-needle"})
        spec = ViewerSpec("legacy-session", "legacy-conversation", "legacy-agent",
                          inspection=Inspection(), input_protocol="keys")
        host = self._view((_text("f"), Checkpoint("filters"), _key("escape"),
                           *_search("late-needle"), Checkpoint("hit")), source=source, spec=spec)
        self.assertIn("outer_field", host.checkpoints["filters"])
        hit = host.checkpoints["hit"]
        self.assertIn("legacy-session", hit)
        self.assertIn("1 matched · 1 records · 1 occurrences", hit)
        self.assertIn("⟦late-needle⟧", hit)
        self.assertEqual(spec.primary_fields, ())
        self.assertIn('"outer_field":', hit)

    def test_every_json_root_uses_generic_public_search_without_headers(self):
        for value, query in (({"keyOnly": "value"}, "KEYONLY"), (["needle", 7], "needle"),
                             ("Straße", "STRASSE"), (27, "27"), (2.5, "2.5"),
                             (True, "TRUE"), (False, "false"), (None, "NULL"), ({}, "{}"), ([], "[]")):
            with self.subTest(value=value):
                host = self._view((*_search(query), Checkpoint("matched"), _key("tab"), _text("j")),
                                  source=jsonl(value), spec=ViewerSpec(input_protocol="keys"))
                frame = host.checkpoints["matched"]
                self.assertIn("1 matched · 1 records · 1 occurrences", frame)
                self.assertNotIn("Session:", frame)
                self.assertIn("Occurrence 1/1", frame)

    def test_members_outside_named_sections_remain_reachable_by_navigation_goto_and_search(self):
        source = jsonl({"message": "header"}, {"message": "tail-only-unsectioned"})
        spec = ViewerSpec(inspection=Inspection(groups=(Group("bundle", "Bundle", (1, 2),
                            sections=(Section("header", "Header", (1,)),)),)), input_protocol="keys")
        host = self._view((_key("tab"), _key("down"), Checkpoint("navigation"), "goto\t1", "goto\t2",
                           Checkpoint("goto"), *_search("tail-only-unsectioned"), Checkpoint("search")),
                          source=source, spec=spec)
        for name in ("navigation", "goto", "search"):
            self.assertIn("tail-only-unsectioned", host.checkpoints[name])
            self.assertIn("Other records", host.checkpoints[name])
        self.assertIn("1 matched · 1 records · 1 occurrences", host.checkpoints["search"])


if __name__ == "__main__":
    unittest.main()
