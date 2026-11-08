"""Exercise generic inspection input, frames, and deterministic cell geometry."""

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

    def test_single_starts_at_first_record_after_an_empty_section_and_reaches_scalar(self):
        source = jsonl({"message": "first-retained"}, {"message": "second-retained"}, "orphan-root")
        spec = ViewerSpec(inspection=Inspection(groups=(Group("bundle", "Bundle", (1, 2),
                            sections=(Section("empty", "Empty"), Section("body", "Body", (1, 2)))),)),
                          input_protocol="keys")
        host = self._view((Checkpoint("initial"), _key("down"), Checkpoint("second"),
                           _key("tab"), _key("down"), _key("enter"), Checkpoint("scalar")),
                          source=source, spec=spec)
        self.assertIn("Single ·", host.checkpoints["initial"])
        self.assertIn("Focus: Detail", host.checkpoints["initial"])
        self.assertIn("[Body]", host.checkpoints["initial"])
        self.assertNotIn("[Empty]", host.checkpoints["initial"])
        self.assertIn("first-retained", host.checkpoints["initial"])
        self.assertIn("second-retained", host.checkpoints["second"])
        self.assertIn("orphan-root", host.checkpoints["scalar"])
        self.assertIn("Focus: Detail", host.checkpoints["scalar"])

    def test_layout_commands_remember_split_across_both_compact_thresholds(self):
        for command in (_text("v"), "line\tv", "toggle_layout"):
            with self.subTest(command=command):
                spec = ViewerSpec(inspection=Inspection(), input_protocol="keys")
                host = self._view((Checkpoint("initial"), command, Checkpoint("split"),
                                   (119, 12), Checkpoint("compact_detail"), (120, 12),
                                   _key("tab"), (119, 12), Checkpoint("narrow"),
                                   (120, 11), Checkpoint("short"), (120, 12), Checkpoint("restored"),
                                   command, Checkpoint("single_list"), _key("enter"),
                                   (200, 32), Checkpoint("detail")),
                                  source=jsonl("layout-marker"), spec=spec, size=(120, 12))
                self.assertIn("Single ·", host.checkpoints["initial"])
                self.assertIn("Split ·", host.checkpoints["split"])
                self.assertIn("Split→Single", host.checkpoints["compact_detail"])
                self.assertIn("Focus: Detail", host.checkpoints["compact_detail"])
                for name in ("narrow", "short"):
                    self.assertIn("Split→Single", host.checkpoints[name])
                    self.assertIn("Focus: List", host.checkpoints[name])
                    self.assertNotIn(" │ ", host.checkpoints[name])
                self.assertIn("Split ·", host.checkpoints["restored"])
                self.assertIn("Focus: List", host.checkpoints["restored"])
                self.assertIn("Single ·", host.checkpoints["single_list"])
                self.assertIn("Focus: Detail", host.checkpoints["detail"])
                self.assertIn("layout-marker", host.checkpoints["detail"])

    def test_tab_enter_and_layout_roundtrips_preserve_the_current_detail(self):
        records = list(audit_records())
        records[6] = dict(records[6], padding="x" * 220,
                          fold_me={"value": "fold-sentinel"}, rows=list(range(40)))
        host = self._view((*_severity_error(), *_search("timeout", messages=True),
                           _text("n"), _text("n"), _text("]"), Checkpoint("written"),
                           PaintedClick('"fold_me"', '"fold_me"'), _key("enter"),
                           Checkpoint("folded"), *("cursor_down" for _ in range(28)),
                           "scroll_right", Checkpoint("before"), _key("tab"), Checkpoint("list"),
                           _key("enter"), Checkpoint("entered"), _text("v"), _text("v"),
                           Checkpoint("toggled"), (119, 32), (200, 32), Checkpoint("resized")),
                          source=jsonl(*records))
        self.assertIn("[Written]", host.checkpoints["written"])
        self.assertIn("saved", host.checkpoints["written"])
        self.assertIn("fold-sentinel", host.checkpoints["written"])
        self.assertNotIn("fold-sentinel", host.checkpoints["folded"])
        self.assertNotEqual(without_caret(host.checkpoints["folded"]),
                            without_caret(host.checkpoints["before"]))
        self.assertIn("Focus: List", host.checkpoints["list"])
        for name in ("entered", "toggled", "resized"):
            self.assertEqual(host.checkpoints["before"], host.checkpoints[name], name)
            self.assertIn("Occurrence 3/3", host.checkpoints[name])
            self.assertIn("Severity=Error", host.checkpoints[name])
            self.assertIn("In: Messages", host.checkpoints[name])

    def test_single_detail_uses_full_width_and_has_no_hidden_list_clicks(self):
        wide = "FULL_WIDTH_" + "x" * 145 + "_TAIL"
        source = jsonl({"message": wide}, {"message": "second-source"})
        spec = ViewerSpec(inspection=Inspection(groups=(Group("first", "First item", (1,)),
                                                        Group("second", "Second item", (2,)))),
                          input_protocol="keys")
        for color in (False, True):
            with self.subTest(color=color):
                host = self._view((Checkpoint("single"), _text("v"), Checkpoint("split"),
                                   _text("v"), PaintedClick("Second item", "Second item", checkpoint="split"),
                                   Checkpoint("replayed"), _text("v"),
                                   PaintedClick("Second item", "Second item"), Checkpoint("clicked"),
                                   _key("enter"), _text("v"), Checkpoint("selected")),
                                  source=source, spec=spec, color=color)
                single, split, replayed, selected = (strip_ansi(host.checkpoints[name])
                                                    for name in ("single", "split", "replayed", "selected"))
                self.assertIn("_TAIL", single)
                self.assertNotIn("_TAIL", split)
                self.assertNotIn("Second item", single)
                self.assertIn("first ·", replayed)
                self.assertIn("annotations", replayed)
                self.assertIn("_TAIL", replayed)
                self.assertNotIn("second-source", replayed)
                self.assertIn("Focus: List", strip_ansi(host.checkpoints["clicked"]))
                self.assertIn("second ·", selected)
                self.assertIn("annotations", selected)
                self.assertIn("second-source", selected)
                self.assertIn("Focus: Detail", selected)
                self.assertTrue(all(_text_cells(line) <= 200 for line in selected.splitlines()))
                if not color:
                    self.assertNotIn("\x1b", host.checkpoints["single"])

    def test_height_resize_preserves_deep_logical_cursor_and_inspection_state(self):
        records = list(audit_records())
        records[6] = dict(records[6], fold_me={"value": "fold-sentinel"},
                          fields={f"field_{index:02}": f"anchor-{index:02}" for index in range(30)})

        def cursor_anchor(frame, *, split):
            lines = strip_ansi(frame).splitlines()
            if split:
                lines = [line.partition(" │ ")[2] if " │ " in line else line for line in lines]
            caret = next(index for index, line in enumerate(lines) if line.strip() == "^")
            self.assertGreater(caret, 0)
            source_row = lines[caret - 1]
            column = lines[caret].index("^")
            self.assertIn('"field_20"', source_row)
            self.assertEqual(source_row[column:column + len("anchor-20")], "anchor-20")

        for split in (False, True):
            with self.subTest(split=split):
                host = self._view((*((_text("v"),) if split else ()), *_severity_error(),
                                   *_search("timeout", messages=True), _text("n"), _text("n"),
                                   _text("]"), PaintedClick('"fold_me"', "{"),
                                   _key("enter"), Checkpoint("folded"),
                                   PaintedClick('"field_20"', "anchor-20"), Checkpoint("deep"),
                                   (200, 14), Checkpoint("compact"),
                                   (200, 50), Checkpoint("restored"), "goto\t7", Checkpoint("fold_check")),
                                  source=jsonl(*records), size=(200, 50))
                self.assertIn('"fold_me"', host.checkpoints["folded"])
                self.assertNotIn("fold-sentinel", host.checkpoints["folded"])
                for name in ("deep", "compact", "restored"):
                    frame = host.checkpoints[name]
                    cursor_anchor(frame, split=split)
                    self.assertIn("[Written]", frame)
                    self.assertIn("Severity=Error", frame)
                    self.assertIn("In: Messages", frame)
                    self.assertIn("Occurrence 3/3", frame)
                    self.assertIn("Focus: Detail", frame)
                    self.assertIn("Split ·" if split else "Single ·", frame)
                self.assertIn('"fold_me"', host.checkpoints["fold_check"])
                self.assertNotIn("fold-sentinel", host.checkpoints["fold_check"])

    def test_modal_v_is_text_or_inert_and_empty_results_keep_layout_controls(self):
        host = self._view((Checkpoint("initial"), _text("/"), _text("v"), Checkpoint("draft"),
                           "toggle_layout", Checkpoint("inert"), _key("escape"), Checkpoint("cancelled"),
                           _text("f"), Checkpoint("filters"), _text("v"), "toggle_layout",
                           Checkpoint("filters_inert"), _key("escape"),
                           *_search("no-such-layout-match"), Checkpoint("empty_single"),
                           _text("v"), Checkpoint("empty_split"), _key("tab"), _text("v"),
                           Checkpoint("empty_list")))
        self.assertIn("v│", host.checkpoints["draft"])
        self.assertEqual(host.checkpoints["draft"], host.checkpoints["inert"])
        self.assertEqual(host.checkpoints["initial"], host.checkpoints["cancelled"])
        self.assertEqual(host.checkpoints["filters"], host.checkpoints["filters_inert"])
        line = self._view((_text("/"), "line\tv", Checkpoint("committed")))
        self.assertIn("Search: 'v'", line.checkpoints["committed"])
        self.assertIn("Single ·", line.checkpoints["committed"])
        for name, layout in (("empty_single", "Single ·"), ("empty_split", "Split ·"),
                             ("empty_list", "Single ·")):
            self.assertIn("No text matches", host.checkpoints[name])
            self.assertIn("0 matched", host.checkpoints[name])
            self.assertIn(layout, host.checkpoints[name])

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
                           Checkpoint("narrow_draft"), _key("escape"), Checkpoint("detail"),
                           _key("tab"), Checkpoint("list"), _key("shift_tab"), Checkpoint("detail_again")))
        self.assertIn("Severity: [Error]", host.checkpoints["wide_draft"])
        self.assertIn("Severity: [Error]", host.checkpoints["narrow_draft"])
        self.assertIn("Focus: List", host.checkpoints["list"])
        self.assertIn("Focus: Detail", host.checkpoints["detail"])
        self.assertIn('"trace": "alpha"', host.checkpoints["detail"])
        self.assertIn("Trace Alpha", host.checkpoints["list"])
        self.assertEqual(host.checkpoints["detail"], host.checkpoints["detail_again"])
        for name in ("narrow_draft", "list", "detail", "detail_again"):
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
        host = self._view((_key("down"), Checkpoint("navigation"), "goto\t1", "goto\t2",
                           Checkpoint("goto"), *_search("tail-only-unsectioned"), Checkpoint("search")),
                          source=source, spec=spec)
        for name in ("navigation", "goto", "search"):
            self.assertIn("tail-only-unsectioned", host.checkpoints[name])
            self.assertIn("Other records", host.checkpoints[name])
        self.assertIn("1 matched · 1 records · 1 occurrences", host.checkpoints["search"])

    def test_name_find_is_literal_unicode_and_preserves_body_conditions_and_hidden_selection(self):
        source = jsonl({"message": "timeout", "kind": "batch"},
                       {"message": "saved", "kind": "batch"},
                       {"message": "timeout", "kind": "sensor"})
        spec = ViewerSpec(inspection=Inspection(groups=(
            Group("batch-104", "Straße 작업", (1, 2)),
            Group("sensor-1", "센서 로그", (3,)))), input_protocol="keys")
        host = self._view(("search\ttimeout", _key("tab"), _text("t"),
                           _text("STRASSE"), Checkpoint("casefold"), _key("enter"),
                           _key("down"), Checkpoint("context"), _key("tab"),
                           _text("t"), _key("ctrl_u"), _text("센서"), Checkpoint("hidden"),
                           _key("enter"), Checkpoint("sensor"), _key("tab"),
                           _text("t"), _key("ctrl_u"), _text("batch-104"),
                           _key("enter"), Checkpoint("restored")), source=source, spec=spec)
        self.assertIn("STRASSE", host.checkpoints["casefold"])
        self.assertIn("1 names", host.checkpoints["casefold"])
        self.assertIn("1/2", host.checkpoints["casefold"])
        self.assertIn('"message": "saved"', host.checkpoints["context"])
        self.assertIn("outside name results; detail retained.", host.checkpoints["hidden"])
        self.assertIn("batch-104", host.checkpoints["hidden"])
        self.assertIn("sensor-1", host.checkpoints["sensor"])
        self.assertIn("Record 2/2", host.checkpoints["restored"])
        self.assertIn('"message": "saved"', host.checkpoints["restored"])
        self.assertIn("Search: 'timeout'", host.checkpoints["restored"])
        filtered = self._view((*_severity_error(), *_search("timeout", messages=True),
                               _key("tab"), _text("t"), _text("Trace Beta"),
                               Checkpoint("none"), _key("enter"), _key("up"), _key("down"),
                               Checkpoint("inert"), _key("escape"), _key("enter"),
                               Checkpoint("detail")))
        self.assertIn("No names match", filtered.checkpoints["none"])
        self.assertEqual(filtered.checkpoints["none"].splitlines()[:-2],
                         filtered.checkpoints["inert"].splitlines()[:-2])
        self.assertIn("No names match", filtered.checkpoints["inert"])
        self.assertIn("Severity=Error", filtered.checkpoints["detail"])
        self.assertIn("In: Messages", filtered.checkpoints["detail"])
        self.assertIn("Search: 'timeout'", filtered.checkpoints["detail"])
        self.assertIn("Trace Alpha", filtered.checkpoints["detail"])

    def test_name_find_text_mode_and_line_commands_do_not_become_main_commands(self):
        class ModeHost(InteractiveHost):
            def __init__(self, events):
                super().__init__(events, size=(180, 30))
                self.modes = []

            def set_input_mode(self, mode):
                self.modes.append(mode)

        description = Inspection(groups=(Group("a", "Alpha", (1,)),
            Group("literal-q", "q", (2,)), Group("literal-up", "up", (3,))))
        spec = ViewerSpec(inspection=description, input_protocol="keys")
        host = ModeHost(("line\ttab", "line\tt", "line\tq", Checkpoint("q"),
                         "line\ttab", "line\tt", _key("ctrl_u"), "line\tup", Checkpoint("up"),
                         _key("tab"), _text("t"), _key("ctrl_u"), _text("p한글"),
                         Checkpoint("text"), _key("escape"), _text("/"), _text("p한글"),
                         _key("escape"), Checkpoint("cancelled"), "close"))
        source = jsonl("alpha", "literal q", "literal up")
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertIn("literal-q", host.checkpoints["q"])
        self.assertIn("literal-up", host.checkpoints["up"])
        self.assertIn("p한글", host.checkpoints["text"])
        self.assertIn("p Dark", host.checkpoints["cancelled"])
        self.assertEqual(host.modes, ["command", "text", "command", "text", "command",
                                     "text", "command", "text", "command"])

    def test_group_navigation_restores_sections_and_evicts_only_after_32_groups(self):
        for count in (32, 33):
            with self.subTest(groups=count):
                source = jsonl(*({"batch": index, "part": part}
                                 for index in range(count) for part in (1, 2)))
                groups = tuple(Group(f"batch-{index}", f"Batch {index}",
                                     (index * 2 + 1, index * 2 + 2), sections=(
                                         Section("first", "First", (index * 2 + 1,)),
                                         Section("second", "Second", (index * 2 + 2,))))
                               for index in range(count))
                events = [_key("down"), _key("tab")]
                for _ in range(1, count):
                    events.extend((_key("down"), _key("enter"), _key("down"), _key("tab")))
                events.extend((_key("down"), _key("enter"), Checkpoint("returned"),
                               _key("down"), Checkpoint("within_group")))
                host = self._view(events, source=source,
                                  spec=ViewerSpec(inspection=Inspection(groups=groups),
                                                  input_protocol="keys"))
                expected = 2 if count == 32 else 1
                self.assertIn(f"Record {expected}/2", host.checkpoints["returned"])
                self.assertIn("[Second]" if expected == 2 else "[First]",
                              host.checkpoints["returned"])
                self.assertIn('"batch": 0', host.checkpoints["within_group"])
        source = jsonl({"message": "timeout"}, {"message": "saved"},
                       {"message": "timeout"}, {"message": "saved"})
        spec = ViewerSpec(inspection=Inspection(groups=(Group("a", "Batch A", (1, 2)),
            Group("b", "Sensor B", (3, 4)))), input_protocol="keys")
        host = self._view((_key("down"), _key("tab"), _key("down"), _key("enter"),
                           _key("down"), "search\ttimeout", _key("tab"), _key("down"),
                           _key("enter"), Checkpoint("new_conditions")), source=source, spec=spec)
        self.assertIn("Record 1/2", host.checkpoints["new_conditions"])
        self.assertIn('"message": "⟦timeout⟧"', host.checkpoints["new_conditions"])

    def test_group_rows_are_single_cell_aligned_with_visible_kinds_and_palette(self):
        from jsonl_viewer._browser import _select
        from jsonl_viewer._browser_render import render_browser
        from jsonl_viewer._catalog import bind_catalog
        from jsonl_viewer._input import parse_jsonl
        from jsonl_viewer._model import BrowserState
        from jsonl_viewer._query import evaluate
        from jsonl_viewer._render import RenderSession

        label = "중복 센서 👩‍💻 é\n" + "가시성" * 20
        source = jsonl(*({"value": i} for i in range(13)))
        groups = (Group("batch-long-104", label, (1,)),
                  Group("batch-long-105", label, (2, 3)),
                  Group("sensor", "record:13", tuple(range(4, 13))))
        spec = ViewerSpec(inspection=Inspection(groups=groups), input_protocol="keys")
        snapshot = parse_jsonl(source)
        catalog = bind_catalog(snapshot, spec.inspection)
        filters = tuple(frozenset() for _ in catalog.facets)
        initial = BrowserState(filters=filters, result=evaluate(snapshot, catalog, filters, None),
                               focus="list", layout="split")
        state = _select(initial, catalog, 0)
        for columns, target in ((120, None), (200, 48)):
            for color in (False, True):
                with self.subTest(columns=columns, color=color):
                    frame = render_browser(spec, state, snapshot, catalog, columns=columns,
                                           rows=28, color=color, session=RenderSession())
                    expected = frame.detail_column - 3
                    if target is None:
                        self.assertTrue(32 <= expected <= 37)
                    else:
                        self.assertEqual(expected, target)
                    hits = [hit for hit in frame.hits if hit.kind == "item"]
                    self.assertEqual(len(hits), 4)
                    self.assertEqual([hit.row for hit in hits], list(range(hits[0].row, hits[0].row + 4)))
                    rows = [strip_ansi(frame.text).splitlines()[hit.row].partition(" │ ")[0]
                            for hit in hits]
                    self.assertTrue(all(_text_cells(row) == expected for row in rows))
                    self.assertTrue(all(_text_cells(line) <= columns
                                        for line in strip_ansi(frame.text).splitlines()))
                    self.assertEqual([row.rstrip()[-1] for row in rows], ["1", "2", "9", "1"])
                    self.assertIn("104", rows[0])
                    self.assertIn("105", rows[1])
                    self.assertRegex(rows[0], r"▸\s*G\s*0?1")
                    self.assertRegex(rows[2], r"\sG\s*0?3")
                    self.assertRegex(rows[3], r"\sR\s*0?4")
                    if color:
                        self.assertIn("48;2;29;78;216", frame.text)
                        self.assertIn("48;2;21;55;80", frame.text)
                    else:
                        self.assertNotIn("\x1b", frame.text)
        short_spec = ViewerSpec(inspection=Inspection(groups=(Group("a", "Batch", (1,)),)))
        short_catalog = bind_catalog(snapshot, short_spec.inspection)
        short_filters = tuple(frozenset() for _ in short_catalog.facets)
        short = _select(replace(initial, filters=short_filters,
                                result=evaluate(snapshot, short_catalog, short_filters, None)),
                        short_catalog, 0)
        short_frame = render_browser(short_spec, short, snapshot, short_catalog, columns=160,
                                     rows=28, color=False, session=RenderSession())
        self.assertEqual(short_frame.detail_column - 3, 40)
        narrow = render_browser(spec, replace(state, layout="single"), snapshot, catalog,
                                columns=32, rows=28, color=False, session=RenderSession())
        narrow_hits = [hit for hit in narrow.hits if hit.kind == "item"]
        self.assertEqual(len(narrow_hits), 4)
        self.assertTrue(all(_text_cells(narrow.text.splitlines()[hit.row]) == 32
                            for hit in narrow_hits))
        for appearance, code in (("light", "48;2;186;230;253"), ("ansi", "97;104")):
            frame = render_browser(spec, replace(state, appearance=appearance), snapshot, catalog,
                                   columns=200, rows=28, color=True, session=RenderSession())
            self.assertIn(code, frame.text)
        frame = render_browser(spec, replace(state, list_query="중복"), snapshot, catalog,
                               columns=200, rows=28, color=True, session=RenderSession())
        self.assertIn("48;2;251;191;36", frame.text)

    def test_selected_full_values_scroll_without_growing_group_rows(self):
        name = "StartName-" + "센서가시성" * 45 + "-NameTail"
        identity = "id-" + "i" * 90 + "-IdentityTail"
        spec = ViewerSpec(inspection=Inspection(groups=(Group(identity, name, (1, 2)),)),
                          input_protocol="keys")
        events = [_key("tab"), Checkpoint("start")]
        for index in range(45):
            events.extend((_text("."), Checkpoint(f"info{index}")))
        events.extend((_text(",") for _ in range(45)))
        events.append(Checkpoint("back"))
        host = self._view(events, source=jsonl("batch", "sensor"), spec=spec, size=(48, 14))
        chunks = {}
        for frame in host.checkpoints.values():
            lines = frame.splitlines()
            marker = lines[-4].split(" ", 2)
            offset = int(marker[1].split("/")[0]) - 1
            chunks[offset] = marker[2].rstrip()
            chunks[offset + 1] = lines[-3].strip()
        information = "".join(chunks[index] for index in sorted(chunks))
        self.assertIn(name, information)
        self.assertIn(identity, information)
        self.assertEqual(host.checkpoints["info43"], host.checkpoints["info44"])
        self.assertEqual(host.checkpoints["start"], host.checkpoints["back"])
        self.assertTrue(all(_text_cells(line) <= 48 for frame in host.checkpoints.values()
                            for line in frame.splitlines()))

    def test_cross_group_return_restores_deep_cursor_folds_and_scroll(self):
        source = jsonl({"message": "batch start"},
            {"fold_me": {"value": "fold-sentinel"},
             "fields": {f"field_{i:02}": f"anchor-{i:02}" for i in range(30)}},
            {"message": "sensor comparison"})
        spec = ViewerSpec(inspection=Inspection(groups=(
            Group("batch", "Batch", (1, 2), sections=(Section("first", "First", (1,)),
                                                       Section("second", "Second", (2,)))),
            Group("sensor", "Sensor", (3,)))), input_protocol="keys")
        host = self._view((_key("down"), PaintedClick('"fold_me"', "{"), _key("enter"),
            PaintedClick('"field_20"', "anchor-20"), _key("right"), _key("right"),
            Checkpoint("before"), _key("tab"), _key("down"), _key("enter"),
            Checkpoint("sensor"), _key("tab"), _key("up"), _key("enter"),
            Checkpoint("after")), source=source, spec=spec, size=(200, 50))
        self.assertIn("[Second]", host.checkpoints["after"])
        self.assertIn("Record 2/2", host.checkpoints["after"])
        self.assertNotIn("fold-sentinel", host.checkpoints["after"])
        self.assertIn("sensor comparison", host.checkpoints["sensor"])
        self.assertEqual(host.checkpoints["before"], host.checkpoints["after"])

    def test_ungrouped_detail_arrows_preserve_ordinary_record_navigation(self):
        source = jsonl({"message": "first audit record"}, "middle sensor record", ["last batch record"])
        host = self._view((Checkpoint("first"), _key("down"), Checkpoint("middle"),
                           _key("down"), Checkpoint("last"), _key("up"), Checkpoint("back")),
                          source=source, spec=ViewerSpec(input_protocol="keys"))
        self.assertIn("first audit record", host.checkpoints["first"])
        self.assertIn("middle sensor record", host.checkpoints["middle"])
        self.assertIn("last batch record", host.checkpoints["last"])
        self.assertIn("middle sensor record", host.checkpoints["back"])
        self.assertNotIn("last batch record", host.checkpoints["back"])


if __name__ == "__main__":
    unittest.main()
