"""Full-text occurrence navigation, visible focus, and bounded search safety."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import unittest
from unittest import mock

import jsonl_viewer._json as json_module
import jsonl_viewer._search as search_module
from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import Occurrence, Record, SearchState, Snapshot, ViewState
from jsonl_viewer._render import _text_cells, render_frame, strip_ansi
from jsonl_viewer._search import SearchLimitError, find_matches

from tests.support import FakeHost, styled_text, without_caret
from tests.test_cursor_and_folding import _View


def _source(*records: object) -> bytes:
    return "".join(json.dumps(record) + "\n" for record in records).encode()


def _body(frame: str) -> str:
    # Search query and the explanatory footer marker cannot satisfy focus assertions.
    return "\n".join(frame.splitlines()[1:-2])


class FullTextOccurrenceTests(unittest.TestCase):
    def test_public_loop_key_search_reveals_all_contiguous_anchor_segments(self):
        source = _source({"need": 0})
        for color in (False, True):
            with self.subTest(color=color):
                host = self._view(source, "search\tneed", size=(12, 8), color=color)
                frame = host.frames[-1]
                self.assertIn("@ 1 │ ⟦need⟧", strip_ansi(frame))
                self.assertTrue(all(_text_cells(line) <= 12 for line in strip_ansi(frame).splitlines()))
                if color:
                    self.assertIn("n", styled_text(frame, "7"))
                else:
                    self.assertIn("^", frame)

    def test_public_loop_search_keeps_whole_bare_keyword_when_only_brackets_overflow(self):
        source = _source({"content": "x" * 100 + "needle" + "y" * 20})
        for color in (False, True):
            with self.subTest(color=color):
                host = self._view(source, "search\tneedle", size=(12, 8), color=color)
                frame = host.frames[-1]
                self.assertIn("@ 1 │ needle", strip_ansi(frame))
                self.assertNotIn("⟦", _body(strip_ansi(frame)))
                self.assertTrue(all(_text_cells(line) <= 12 for line in strip_ansi(frame).splitlines()))
                if color:
                    self.assertIn("n", styled_text(frame, "7"))
                else:
                    self.assertIn("^", frame)

    def setUp(self) -> None:
        self.spec = ViewerSpec("session", "conversation", "agent")

    def _view(self, source, *events, keys=False, size=(240, 100), color=False):
        before = hashlib.sha256(source).digest()
        spec = ViewerSpec("session", "conversation", "agent", input_protocol="keys") if keys else self.spec
        host = FakeHost((*events, "close"), size=size, color=color)
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), before)
        return host

    def _result(self, frame, query, count):
        self.assertIn(f"{count} occurrences • Search all text={query!r}", frame)

    def test_repeated_keywords_traverse_each_key_value_and_record_in_order(self):
        source = _source({"content": {"hit": "hit hit", "other": ["hit"]}}, {"content": "hit"})
        hits = find_matches(parse_jsonl(source), "hit")
        self.assertEqual([(h.record_index, h.path, h.kind, h.start, h.end) for h in hits], [
            (0, ("content", "hit"), "key", 0, 3),
            (0, ("content", "hit"), "value", 0, 3),
            (0, ("content", "hit"), "value", 4, 7),
            (0, ("content", "other", 0), "value", 0, 3),
            (1, ("content",), "value", 0, 3),
        ])
        host = self._view(source, "search\thit", *("next_match",) * 5, "previous_match")
        for frame, ordinal in zip(host.frames[2:], (1, 2, 3, 4, 5, 1, 5)):
            self._result(frame, "hit", f"{ordinal}/5")
            self.assertEqual(_body(frame).count("⟦hit⟧"), 1)
        self.assertIn('"⟦hit⟧": "hit hit"', _body(host.frames[2]))
        self.assertIn('"hit": "⟦hit⟧ hit"', _body(host.frames[3]))
        self.assertIn('"hit": "hit ⟦hit⟧"', _body(host.frames[4]))

    def test_casefold_expansions_map_back_to_original_codepoints_without_duplicates(self):
        for value, query, expected in (("Straße STRASSE", "strasse", [(0, 6), (7, 14)]),
                                       ("ßs", "s", [(0, 1), (1, 2)]),
                                       ("aaaa", "aa", [(0, 2), (2, 4)]),
                                       ("İ i", "i", [(0, 1), (2, 3)])):
            with self.subTest(value=value, query=query):
                hits = find_matches(parse_jsonl(_source(value)), query)
                self.assertEqual([(h.start, h.end) for h in hits], expected)
                host = self._view(_source(value), "search\t" + query)
                self.assertIn("⟦" + value[expected[0][0]:expected[0][1]] + "⟧", _body(host.frames[-1]))

    def test_decoded_json_precedence_avoids_duplicate_raw_representations(self):
        encoded = json.dumps({"items": [{"status": "Straße 개요 Straße 개요"}]})
        source = _source({"content": "orientation", "payload": json.dumps(encoded)})
        host = self._view(source, "search\tSTRASSE 개요", "next_match")
        for ordinal, frame in enumerate(host.frames[2:], 1):
            self._result(frame, "STRASSE 개요", f"{ordinal}/2")
            self.assertIn("VERBOSE", frame)
            self.assertEqual(_body(frame).count("⟦Straße 개요⟧"), 1)
        self.assertIn('"status": "Straße 개요 ⟦Straße 개요⟧"', _body(host.frames[-1]))

    def test_all_json_roots_keys_scalars_and_empty_containers_are_searchable(self):
        for value, query in (({"keyOnly": "value"}, "KEYONLY"), (["needle", 7], "needle"),
                             ("Straße", "STRASSE"), (27, "27"), (2.5, "2.5"),
                             (True, "TRUE"), (False, "false"), (None, "NULL"), ({}, "{}"), ([], "[]")):
            with self.subTest(value=value):
                host = self._view(_source(value), "search\t" + query)
                self._result(host.frames[-1], query, "1/1")
                self.assertIn("⟦", _body(host.frames[-1]))

    def test_raw_and_normalized_fallbacks_show_the_exact_matched_excerpt(self):
        for value, query, kind in (({"payload": {"z": 2, "a": True}}, '{"a":true,"z":2}', "normalized"),
                                   ({"payload": json.dumps({"text": "line1\nline2"})}, ': ', "raw")):
            with self.subTest(kind=kind):
                hits = find_matches(parse_jsonl(_source(value)), query)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].kind, kind)
                host = self._view(_source(value), "search\t" + query)
                body = _body(host.frames[-1])
                self.assertIn(f"[{kind} search excerpt]", body)
                self.assertIn("⟦", body)
                self.assertIn("⟦" + json.dumps(query, ensure_ascii=False)[1:-1] + "⟧", body)

    def test_non_json_strings_remain_literal_without_partial_expansion(self):
        for encoded in ('prefix {"value":"needle"}', '{"value":"needle"} trailing',
                        '{"value":"needle",}', '{"value":"needle","value":"other"}',
                        '{"value":"needle","bad":NaN}', '{"value":"needle","bad":Infinity}',
                        '{"value":"needle","bad":-Infinity}', '{"value":"needle","bad":1e999}'):
            with self.subTest(encoded=encoded):
                host = self._view(_source({"payload": encoded}), "search\tneedle")
                self._result(host.frames[-1], "needle", "1/1")
                self.assertNotIn("[expanded JSON string", _body(host.frames[-1]))
                self.assertIn("⟦needle⟧", _body(host.frames[-1]))

    def test_entire_line_phrase_and_selector_punctuation_are_literal_queries(self):
        source = _source({"content": "two words payload.items[0].status /pointer"})
        for command, query in (("/ two words", "two words"),
                               ("/ payload.items[0].status", "payload.items[0].status"),
                               ("/ /pointer", "/pointer"), ("// two words", "two words")):
            with self.subTest(command=command):
                host = self._view(source, "line\t" + command, keys=True)
                self._result(host.frames[-1], query, "1/1")
                self.assertIn("⟦" + query + "⟧", _body(host.frames[-1]))
        host = self._view(source, "search\ttwo words", "search\tcontent\ttwo words")
        self._result(host.frames[-1], "two words", "1/1")
        self.assertIn("field search was removed", host.frames[-1])

    def test_one_query_prompt_and_cancel_preserve_current_occurrence(self):
        source = _source({"content": "needle needle"})
        for escape in ("key\tescape", "key\tunknown_escape"):
            host = self._view(source, "text\t/", "text\tneedle", "key\tenter", "text\tn",
                              "text\t/", "text\tdraft", escape, "text\tN", keys=True)
            self.assertIn("Search query:", host.frames[2])
            self.assertFalse(any("Search field:" in f for f in host.frames))
            self.assertEqual(host.frames[5], host.frames[-2])
            self._result(host.frames[-1], "needle", "1/2")
        reopened = self._view(source)
        self.assertNotIn("Search all text", reopened.frames[-1])

    def test_query_limits_invalid_controls_and_removed_events_are_transactional(self):
        source = _source({"content": "x" * 1024})
        host = self._view(source, "search\t" + "x" * 1024, "search\t" + "x" * 1025,
                          "search\t", "search\tcontent\tx", "search\tx\ny")
        for frame, message in zip(host.frames[3:], ("too long", "must not be empty", "field search was removed", "unsupported controls")):
            self.assertIn(message, frame)
            self.assertIn("1/1 occurrences", frame)
            self.assertIn("⟦", _body(frame))

    def test_dense_occurrence_limit_is_exact_and_failed_search_keeps_committed_state(self):
        source = _source({"content": "x" * 100_000 + " safe"})
        hits = find_matches(parse_jsonl(source), "x")
        self.assertEqual(len(hits), 100_000)
        with self.assertRaises(SearchLimitError):
            find_matches(parse_jsonl(_source({"content": "x" * 100_001})), "x")
        with mock.patch.object(search_module, "MAX_OCCURRENCES", 2):
            host = self._view(_source({"content": "safe xxx"}), "search\tsafe", "search\tx")
        self.assertIn("refine the query", host.frames[-1])
        self._result(host.frames[-1], "safe", "1/1")
        self.assertIn("⟦safe⟧", _body(host.frames[-1]))

    def test_long_preview_width_and_paging_reveal_each_keyword(self):
        source = _source({"content": "x" * 65_536 + "needle" + "y" * 500 + "needle"})
        host = self._view(source, "search\tneedle", "next_match", "previous_match", size=(40, 8))
        for frame, ordinal in zip(host.frames[2:], (1, 2, 1)):
            self.assertIn(f"{ordinal}/2 occurrences", frame)
            self.assertIn("⟦needle⟧", _body(frame))
            self.assertTrue(all(_text_cells(line) <= 40 for line in frame.splitlines()))

    def test_late_nested_match_scrolls_into_view_and_navigation_recovers_after_manual_paging(self):
        source = _source({"content": {**{str(i): "before" for i in range(40)}, "last": "needle needle"}})
        host = self._view(source, "search\tneedle", "page_up", "next_match", size=(60, 9))
        self.assertIn("⟦needle⟧", _body(host.frames[2]))
        self.assertNotIn("⟦needle⟧", _body(host.frames[3]))
        self.assertIn('needle ⟦needle⟧', _body(host.frames[4]))

    def test_resize_reveals_active_keyword_at_new_width(self):
        class ResizingHost(FakeHost):
            def read_event(self):
                event = super().read_event()
                if event == "next_match":
                    self._size = (18, 5)
                return event
        host = ResizingHost(("search\tneedle", "next_match", "close"), size=(100, 24))
        view_jsonl(_source({"content": "x" * 200 + "needle needle"}), self.spec, host)
        self.assertIn("⟦needle⟧", _body(host.frames[-1]))
        self.assertTrue(all(_text_cells(line) <= 18 for line in host.frames[-1].splitlines()))

    def test_twelve_columns_and_five_digit_gutter_keep_visible_wide_keyword(self):
        source = b'{"content":"x"}\n' * 9999 + _source({"content": "x" * 100 + "개"})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, size=(12, 5), color=color)
                result = view.step("search\t개")
                plain = strip_ansi(result.text)
                focused = next(cell for cell in result.characters if cell.position == result.cursor)
                self.assertEqual(focused.text, "개")
                self.assertTrue(focused.search_focus)
                self.assertIn('@ 10000 │ 개', plain)
                self.assertEqual(len(view.state.search.occurrences), 1)
                self.assertEqual(view.state.search.current_index, 0)
                if color:
                    self.assertIn("개", styled_text(result.text, "7"))
                else:
                    self.assertEqual(plain.splitlines()[focused.screen_row + 1],
                                     " " * focused.screen_column + "^")
                self.assertTrue(all(_text_cells(line) <= 12 for line in plain.splitlines()))

    def test_escaped_quotes_backslashes_and_unsafe_characters_focus_original_span(self):
        for value, query, visible in ((r'a "quote" b', '"quote"', r'\"quote\"'),
                                      (r'a \path b', r'\path', r'\\path'),
                                      ('a\u202eb', 'a', 'a')):
            with self.subTest(query=query):
                host = self._view(_source({"content": value}), "search\t" + query)
                self.assertIn("⟦" + visible + "⟧", _body(host.frames[-1]))
                self.assertNotIn('\u202e', host.frames[-1])

    def test_controls_unicode_geometry_and_ansi_parity_remain_safe(self):
        source = _source({"payload": json.dumps({"value": "개요😀e\u0301\x1b[2J\u202e\u2028\ud800"})})
        for size in ((12, 4), (32, 8), (120, 30)):
            plain = self._view(source, "search\t개요", "next_match", size=size)
            ansi = self._view(source, "search\t개요", "next_match", size=size, color=True)
            if size[1] == 30:
                self.assertEqual([without_caret(f) for f in plain.frames],
                                 [strip_ansi(f) for f in ansi.frames])
            for frame in [*plain.frames, *(strip_ansi(f) for f in ansi.frames)]:
                frame.encode("utf-8", errors="strict")
                self.assertNotRegex(frame, r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u202e\u2028\ud800]")
                self.assertLessEqual(len(frame.splitlines()), size[1])
                self.assertTrue(all(_text_cells(line) <= size[0] for line in frame.splitlines()))
            self.assertIn("⟦개요⟧", plain.frames[-1])

    def test_search_has_no_expression_execution_or_external_resources(self):
        with mock.patch("builtins.open") as opened, mock.patch.object(socket, "socket") as network, mock.patch.object(subprocess, "Popen") as process:
            host = self._view(_source({"content": "literal .* [0]"}), "search\t.*", "search\t^literal", "search\t__class__")
        opened.assert_not_called()
        network.assert_not_called()
        process.assert_not_called()
        for frame, query, count in zip(host.frames[2:], (".*", "^literal", "__class__"), ("1/1", "0/0", "0/0")):
            self._result(frame, query, count)

    def test_real_encoding_layer_and_depth_limits_prevent_partial_decoded_hits(self):
        encoded = json.dumps({"leaf": "Ω"})
        for _ in range(15):
            encoded = json.dumps(encoded)
        cases = [(encoded, 1), (json.dumps(encoded), 0)]
        for depth in (63, 64):
            value = "Ω"
            for _ in range(depth):
                value = [value]
            cases.append((json.dumps(value), int(depth == 63)))
        for value, count in cases:
            with self.subTest(count=count, size=len(value)):
                self.assertEqual(len(find_matches(parse_jsonl(_source(value)), "Ω")), count)

    def test_node_and_byte_expansion_limits_are_per_record(self):
        first, second = json.dumps({"name": "α"}), json.dumps({"name": "β"})
        payload = json.dumps({"first": first, "second": second})
        for constant, limit in (("MAX_DERIVED_VALUE_NODES", 5),
                                ("MAX_CUMULATIVE_DECODED_UTF8_BYTES", len(payload.encode()) + len(first.encode()))):
            with self.subTest(constant=constant), mock.patch.object(json_module, constant, limit):
                snapshot = parse_jsonl(_source({"payload": payload}, {"payload": payload}))
                self.assertEqual([h.record_index for h in find_matches(snapshot, "α")], [0, 1])
                self.assertEqual(find_matches(snapshot, "β"), ())

    def test_record_marker_lookup_is_bounded_for_large_sparse_results(self):
        class CountedIndex(int):
            calls = 0
            def __lt__(self, other):
                type(self).calls += 1
                return super().__lt__(other)
        hits = tuple(Occurrence(CountedIndex(i), (), "value", 0, 1, "x") for i in range(1, 8192, 2))
        state = SearchState("x", hits, len(hits) - 1)
        for index, expected in ((0, False), (1, True), (4095, True), (8191, True), (8192, False)):
            CountedIndex.calls = 0
            self.assertEqual(state.has_record(index), expected)
            self.assertLessEqual(CountedIndex.calls, 16)


if __name__ == "__main__":
    unittest.main()
