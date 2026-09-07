"""Behavioral coverage for unrestricted literal JSONL searches."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import unicodedata
import unittest
from unittest import mock

import jsonl_viewer._json as json_module
from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._render import strip_ansi

from tests.support import FakeHost


def _source(*records: object) -> bytes:
    # ASCII JSON escapes also let fixtures distinguish decoded Unicode hits
    # from text that is literally present in a string-encoded container.
    return "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")


class UnrestrictedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = ViewerSpec("session", "conversation", "agent", ())

    def _view(
        self,
        source: bytes,
        *events: str | None,
        keys: bool = False,
        size: tuple[int, int] = (240, 100),
        color: bool = False,
    ) -> FakeHost:
        original = hashlib.sha256(source).digest()
        spec = (
            ViewerSpec("session", "conversation", "agent", (), input_protocol="keys")
            if keys else self.spec
        )
        host = FakeHost((*events, "close"), size=size, color=color)
        view_jsonl(source, spec, host)
        self.assertEqual(host.close_calls, 1)
        self.assertEqual(hashlib.sha256(source).digest(), original)
        return host

    def _result(self, frame: str, field: str, query: str, count: str) -> None:
        self.assertIn(f"Search {field or 'all text'}={query!r} • {count}", frame)

    def test_empty_presets_find_hidden_multiply_encoded_unicode(self) -> None:
        response = json.dumps({"items": [{"status": "Straße 개요"}]})
        payload = json.dumps({"response": json.dumps(response)})
        source = _source({"content": "orientation", "payload": payload})
        self.assertNotIn("Straße".encode(), source)
        host = self._view(source, "search\t\tSTRASSE 개요")
        self._result(host.frames[-1], "", "STRASSE 개요", "1/1")
        self.assertIn("READ ONLY • VERBOSE", host.frames[-1])
        self.assertIn("Hidden-field match selected", host.frames[-1])
        self.assertIn('"status": "Straße 개요"', host.frames[-1])
        self.assertIn("@ 1 │", host.frames[-1])

    def test_arbitrary_fields_dotted_indexes_and_pointers_resolve_same_value(self) -> None:
        payload = {"items": [{"status": "other"}, {"status": "Ready 개요"}]}
        for value in (payload, json.dumps(payload), json.dumps(json.dumps(payload))):
            for field in (
                "payload.items[1].status", "/payload/items/1/status",
                "payload.items[1]", "payload",
            ):
                with self.subTest(encoded=isinstance(value, str), field=field):
                    query = "ready" if field != "payload" else "status"
                    host = self._view(
                        _source({"content": "safe", "payload": value}),
                        f"search\t{field}\t{query}",
                    )
                    self._result(host.frames[-1], field, query, "1/1")
                    self.assertIn('"status": "Ready 개요"', host.frames[-1])

    def test_root_arrays_support_bracket_and_pointer_indexes(self) -> None:
        for root in (
            [{"status": "other"}, [{"status": "needle"}]],
            json.dumps([{"status": "other"}, [{"status": "needle"}]]),
        ):
            for field in ("[1][0].status", "/1/0/status"):
                with self.subTest(root_type=type(root).__name__, field=field):
                    host = self._view(_source(root), f"search\t{field}\tneedle")
                    self._result(host.frames[-1], field, "needle", "1/1")

    def test_exact_top_level_keys_win_before_any_path_interpretation(self) -> None:
        cases = (
            ("payload.status", {"payload": {"status": "nested"}}),
            ("items[0]", {"items": ["nested"]}),
            ("/payload/status", {"payload": {"status": "nested"}}),
            ("/", {"": "nested"}),
            ("payload[", {}),
            ("/bad~2", {}),
        )
        for field, other in cases:
            with self.subTest(field=field):
                source = _source({**other, field: "literal"}, {"unrelated": 0})
                host = self._view(
                    source, f"search\t{field}\tliteral", f"search\t{field}\tnested",
                )
                self._result(host.frames[2], field, "literal", "1/1")
                self._result(host.frames[3], field, "nested", "0/0")
                self.assertNotIn("Malformed field path", host.frames[2])
                self.assertNotIn("JSON Pointer escapes", host.frames[2])

    def test_literal_precedence_is_per_record_with_path_fallback(self) -> None:
        source = _source(
            {"payload.status": "literal", "payload": {"status": "needle"}},
            {"payload": {"status": "needle"}},
        )
        host = self._view(source, "search\tpayload.status\tneedle")
        self._result(host.frames[-1], "payload.status", "needle", "1/1")
        self.assertIn("@ 2 │", host.frames[-1])
        self.assertNotIn("@ 1 │", host.frames[-1])

    def test_pointer_escaping_empty_segments_and_literal_punctuation(self) -> None:
        source = _source({
            "a/b": {"~key": {"": "slash-tilde-empty"}},
            "a.b": {"x[0]": "punctuation"},
            "": {"": "two empty keys"},
            "digits": {"00": "object numeric key"},
        })
        for field, query in (
            ("/a~1b/~0key/", "slash-tilde-empty"),
            ("/a.b/x[0]", "punctuation"),
            ("//", "two empty keys"),
            ("/digits/00", "object numeric key"),
        ):
            with self.subTest(field=field):
                host = self._view(source, f"search\t{field}\t{query}")
                self._result(host.frames[-1], field, query, "1/1")
        host = self._view(_source({"": "empty key"}), "search\t/\tempty key")
        self._result(host.frames[-1], "/", "empty key", "1/1")

    def test_all_json_root_types_and_empty_containers_are_searchable(self) -> None:
        for value, query in (
            ({"keyOnly": "value"}, "KEYONLY"), (["needle", 7], "needle"),
            ("Straße", "STRASSE"), (27, "27"), (2.5, "2.5"),
            (True, "TRUE"), (False, "false"), (None, "NULL"),
            ({}, "{}"), ([], "[]"),
        ):
            with self.subTest(value=value):
                host = self._view(_source(value), f"search\t\t{query}")
                self._result(host.frames[-1], "", query, "1/1")
                self.assertIn("@ 1 │", host.frames[-1])

    def test_full_text_matches_keys_scalars_and_normalized_container_text(self) -> None:
        source = _source({"payload": {"z": 2, "a": True, "keyOnly": None}})
        for query in ("KEYONLY", "true", "null", "2"):
            with self.subTest(query=query):
                host = self._view(source, f"search\t\t{query}")
                self._result(host.frames[-1], "", query, "1/1")
        source = _source({"payload": {"z": 2, "a": True}})
        host = self._view(
            source, 'search\tpayload\t{"a":true,"z":2}',
            'search\tpayload\t{"z": 2, "a": true}',
        )
        self._result(host.frames[2], "payload", '{"a":true,"z":2}', "1/1")
        self._result(host.frames[3], "payload", '{"z": 2, "a": true}', "0/0")

    def test_selected_encoded_string_keeps_literal_substring_semantics(self) -> None:
        source = _source({"payload": json.dumps({"text": "line1\nline2"})})
        host = self._view(
            source, "search\tpayload\t\\n", "search\tpayload\tline1\nline2",
            "search\tpayload.text\tline1\nline2", "search\tpayload.text\t\\n",
            "search\t\tline1\nline2",
        )
        for frame, count in zip(host.frames[2:], ("1/1", "0/0", "1/1", "0/0", "1/1")):
            self.assertIn(f"• {count} • @ current", frame)
        self.assertIn('"text": "line1\\nline2"', host.frames[2])

    def test_only_complete_strict_encoded_containers_allow_deeper_search(self) -> None:
        invalid = (
            'prefix {"value":"needle"}', '{"value":"needle"} trailing',
            '{"value":"needle",}', '{"value":"needle","value":"other"}',
            '{"value":"needle","bad":NaN}', '{"value":"needle","bad":Infinity}',
            '{"value":"needle","bad":-Infinity}', '{"value":"needle","bad":1e999}',
            json.dumps("needle"), "7", "true", "null",
        )
        for encoded in invalid:
            with self.subTest(encoded=encoded):
                host = self._view(
                    _source({"payload": encoded}), "search\tpayload.value\tneedle",
                )
                self._result(host.frames[-1], "payload.value", "needle", "0/0")
                self.assertNotIn("[expanded JSON string", host.frames[-1])
        literal = self._view(
            _source({"payload": invalid[3]}), "search\t\tneedle",
        )
        self._result(literal.frames[-1], "", "needle", "1/1")

    def test_record_hits_are_deduplicated_and_navigation_wraps_in_source_order(self) -> None:
        source = _source(
            {"content": {"hit": ["hit", {"again": "hit"}]}},
            {"content": "unrelated"}, {"content": "hit"}, ["hit", "hit"],
        )
        host = self._view(
            source, "search\t\thit", "next_match", "next_match", "next_match",
            "previous_match",
        )
        for frame, ordinal, line in zip(host.frames[2:], (1, 2, 3, 1, 3), (1, 3, 4, 1, 4)):
            self._result(frame, "", "hit", f"{ordinal}/3")
            self.assertIn(f"@ {line} │", frame)
        self.assertIn("* 3 │", host.frames[2])
        self.assertIn("* 4 │", host.frames[2])

    def test_full_values_remain_searchable_after_both_preview_limits(self) -> None:
        text = "x" * 65_536 + "tail needle"
        source = _source(
            {"content": text}, {"payload": json.dumps([{"value": text}])},
        )
        host = self._view(
            source, "search\t\ttail needle", "next_match",
            "search\tpayload[0].value\ttail needle", "search\tcontent\ttail needle",
            size=(120, 30),
        )
        for frame, field, count in zip(
            host.frames[2:], ("", "", "payload[0].value", "content"),
            ("1/2", "2/2", "1/1", "1/1"),
        ):
            self._result(frame, field, "tail needle", count)
            body = "\n".join(line for line in frame.splitlines() if "│" in line)
            self.assertNotIn("tail needle", body)
            self.assertIn("[truncated", body)
        self.assertIn("READ ONLY • VERBOSE", host.frames[3])

    def test_nested_array_highlighting_marks_only_the_matched_leaf(self) -> None:
        source = _source(
            {"content": [{"status": "hit"}, {"status": "other"}]},
            {"content": [{"status": "hit"}, {"status": "other"}]},
        )
        event = "search\tcontent[0].status\thit"
        plain = self._view(source, event)
        ansi = self._view(source, event, color=True)
        self.assertEqual(plain.frames, [strip_ansi(frame) for frame in ansi.frames])
        hits = [line.partition("│")[2] for line in ansi.frames[-1].splitlines() if '"hit"' in line]
        misses = [line.partition("│")[2] for line in ansi.frames[-1].splitlines() if '"other"' in line]
        self.assertEqual(len(hits), 2)
        self.assertEqual(len(misses), 2)
        self.assertIn('\x1b[1;30;43m"hit"', hits[0])
        self.assertIn('\x1b[4;33m"hit"', hits[1])
        for line in misses:
            self.assertNotIn("\x1b[1;30;43m", line)
            self.assertNotIn("\x1b[4;33m", line)

    def test_next_and_previous_promote_a_new_hidden_hit(self) -> None:
        source = _source({"content": "needle"}, {"payload": {"value": "needle"}})
        for action in ("next_match", "previous_match"):
            with self.subTest(action=action):
                host = self._view(source, "search\t\tneedle", action, "toggle_mode")
                self.assertIn("READ ONLY • SIMPLE", host.frames[2])
                self.assertIn("READ ONLY • VERBOSE", host.frames[3])
                self._result(host.frames[3], "", "needle", "2/2")
                self.assertIn('"value": "needle"', host.frames[3])
                self.assertIn("Verbose mode is required", host.frames[4])

    def test_missing_paths_and_noncanonical_pointer_indexes_are_zero_matches(self) -> None:
        source = _source({"payload": [{"value": "needle"}], "scalar": 7})
        for field in (
            "missing", "payload[4].value", "payload.value", "scalar.value",
            "/payload/00/value", "/payload/-/value", "/payload/-1/value",
            "/payload/99999999999999999999999999999999999999999999999/value",
            "__class__",
        ):
            with self.subTest(field=field):
                host = self._view(source, f"search\t{field}\tneedle", "next_match")
                self._result(host.frames[2], field, "needle", "0/0")
                self.assertIn("No search matches are active", host.frames[3])

    def test_malformed_selectors_preserve_committed_search_until_corrected(self) -> None:
        source = _source({"content": "needle", "payload": {"value": "needle"}})
        for field in ("payload[", "payload..value", "payload.", "payload[-1]", "payload[01]", "payload[*]", "payload['value']", "/bad~2", "/bad~"):
            with self.subTest(field=field):
                host = self._view(
                    source, "search\tcontent\tneedle", f"search\t{field}\tneedle",
                    "search\tpayload.value\tneedle",
                )
                self._result(host.frames[3], "content", "needle", "1/1")
                self.assertRegex(host.frames[3], "Malformed field path|nonnegative integer|Pointer escapes")
                self._result(host.frames[4], "payload.value", "needle", "1/1")

    def test_blank_field_prompt_supports_correction_and_keeps_text_literal(self) -> None:
        host = self._view(
            _source({"payload": "qjknN / needle"}),
            "text\t/", "text\tpayload[", "key\tenter", "key\tctrl_u", "key\tenter",
            "key\tenter", "text\twrong", "key\tctrl_u", "text\tqjknN / needle",
            "key\tenter", keys=True,
        )
        self.assertIn("Malformed field path", host.frames[4])
        self.assertIn("Search query: ", host.frames[6])
        self.assertIn("Search query must not be empty", host.frames[7])
        self._result(host.frames[-1], "", "qjknN / needle", "1/1")

    def test_line_full_text_and_legacy_field_commands_keep_distinct_grammar(self) -> None:
        source = _source({"payload": {"value": "two words"}, "content": "single"})
        for command, field, query, count in (
            ("// two words", "", "two words", "1/1"),
            ("/ single", "", "single", "1/1"),
            ("/ payload.value two words", "payload.value", "two words", "1/1"),
            ("/ content single", "content", "single", "1/1"),
            ("/ two words", "two", "words", "0/0"),
        ):
            with self.subTest(command=command):
                host = self._view(source, "line\t" + command, keys=True)
                self._result(host.frames[-1], field, query, count)

    def test_semantic_field_and_query_limits_are_exact_and_transactional(self) -> None:
        field, query = "f" * 128, "x" * 1024
        source = _source({field: query})
        host = self._view(
            source, f"search\t{field}\t{query}",
            "search\t" + "f" * 129 + "\tx", "search\t\t" + "x" * 1025,
            "search\t\t", "search\tbad\nfield\tx",
        )
        self.assertIn("@ 1 │", host.frames[2])
        self.assertIn("Search " + field + "=", host.frames[2])
        for frame, error in zip(host.frames[3:], (
            "128 characters maximum", "Search query is too long", "must not be empty",
            "control separator",
        )):
            self.assertIn(error, frame)
            self.assertIn("Search " + field + "=", frame)
            self.assertIn("@ 1 │", frame)

    def test_new_search_drafts_cancel_without_losing_results_and_reopen_is_clean(self) -> None:
        source = _source({"content": "needle"}, {"content": "needle"})
        for draft in (
            ("text\t/", "text\tpayload["),
            ("text\t/", "key\tenter", "text\tdraft"),
            ("text\tg", "text\t999"),
        ):
            for escape in ("key\tescape", "key\tunknown_escape"):
                with self.subTest(draft=draft, escape=escape):
                    host = self._view(
                        source, "search\t\tneedle", "text\tn", *draft, escape,
                        "text\tN", keys=True,
                    )
                    self.assertEqual(host.frames[3], host.frames[-2])
                    self._result(host.frames[-1], "", "needle", "1/2")
        closed = self._view(source, "search\t\tneedle", "cancel", "cancel", "cancel")
        self.assertIn("Search cleared", closed.frames[3])
        self.assertNotIn("Search all text", closed.frames[-1])
        reopened = self._view(source)
        self.assertEqual(reopened.frames[-1], closed.frames[1])

    def test_search_does_not_evaluate_expressions_or_acquire_external_resources(self) -> None:
        source = _source({"content": "literal .* [0]", "payload": {"status": "needle"}})
        with (
            mock.patch("builtins.open") as opened,
            mock.patch.object(socket, "socket") as network,
            mock.patch.object(subprocess, "Popen") as process,
        ):
            host = self._view(source, "search\t\t.*", "search\t\t^literal", "search\t__class__\tdict")
        opened.assert_not_called()
        network.assert_not_called()
        process.assert_not_called()
        self._result(host.frames[2], "", ".*", "1/1")
        self._result(host.frames[3], "", "^literal", "0/0")
        self._result(host.frames[4], "__class__", "dict", "0/0")

    def test_controls_unicode_geometry_and_ansi_parity_remain_safe_during_search(self) -> None:
        value = "개요😀e\u0301\x1b[2J\u202e\u2028\ud800"
        source = _source({"payload": json.dumps({"value": value})})
        events = ("search\t\t" + value, "next_match", "previous_match")
        for size in ((12, 4), (32, 8), (120, 30)):
            with self.subTest(size=size):
                plain = self._view(source, *events, size=size)
                ansi = self._view(source, *events, size=size, color=True)
                self.assertEqual(plain.frames, [strip_ansi(frame) for frame in ansi.frames])
                for frame in plain.frames:
                    frame.encode("utf-8", errors="strict")
                    self.assertNotRegex(frame, r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u202e\u2028\ud800]")
                    self.assertLessEqual(len(frame.splitlines()), size[1])
                    for line in frame.splitlines():
                        cells = sum(
                            0 if unicodedata.combining(char) else
                            2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
                            for char in line
                        )
                        self.assertLessEqual(cells, size[0])
                if size[0] == 120:
                    self.assertIn("• 1/1 • @ current", plain.frames[-1])
                    self.assertIn(r"\u001b[2J\u202e\u2028\ud800", plain.frames[-1])

    def test_real_encoding_layer_limit_applies_to_paths_and_decoded_full_text(self) -> None:
        encoded = json.dumps({"leaf": "Ω"})
        for _ in range(15):
            encoded = json.dumps(encoded)
        for value, count in ((encoded, "1/1"), (json.dumps(encoded), "0/0")):
            with self.subTest(count=count):
                host = self._view(
                    _source({"payload": value}), "search\tpayload.leaf\tΩ", "search\t\tΩ",
                )
                self._result(host.frames[2], "payload.leaf", "Ω", count)
                self._result(host.frames[3], "", "Ω", count)
                if count == "0/0":
                    verbose = self._view(_source({"payload": value}), "toggle_mode")
                    self.assertIn("encoding layer limit", verbose.frames[-1])

    def test_real_projected_depth_limit_prevents_partial_decoded_search(self) -> None:
        for depth, count in ((63, "1/1"), (64, "0/0")):
            with self.subTest(depth=depth):
                value: object = "Ω"
                for _ in range(depth):
                    value = [value]
                field = "/0" * depth
                host = self._view(
                    _source(json.dumps(value)), f"search\t{field}\tΩ", "search\t\tΩ",
                )
                if count == "0/0":
                    # The 128-character selector clips the combined footer;
                    # its leading no-match status remains the visible result.
                    self.assertIn(f"No matches in {field}.", host.frames[2])
                    self.assertNotIn("@ 1 │", host.frames[2])
                else:
                    self._result(host.frames[2], field, "Ω", count)
                self._result(host.frames[3], "", "Ω", count)
                if count == "0/0":
                    self.assertIn("display depth limit", host.frames[-1])

    def test_node_and_byte_budgets_are_per_record_with_bounded_render_fallback(self) -> None:
        first = json.dumps({"name": "α"})
        second = json.dumps({"name": "β"})
        payload = json.dumps({"first": first, "second": second})
        source = _source({"payload": payload}, {"payload": payload})
        for constant, limit, reason in (
            ("MAX_DERIVED_VALUE_NODES", 5, "derived node limit"),
            ("MAX_CUMULATIVE_DECODED_UTF8_BYTES", len(payload.encode()) + len(first.encode()), "decoded byte limit"),
        ):
            with self.subTest(constant=constant):
                with mock.patch.object(json_module, constant, limit):
                    host = self._view(
                        source, "search\t\tα", "search\t\tβ",
                        "search\tpayload.second.name\tβ", "next_match",
                    )
                self._result(host.frames[2], "", "α", "1/2")
                self._result(host.frames[3], "", "β", "0/0")
                self._result(host.frames[4], "payload.second.name", "β", "1/2")
                self._result(host.frames[5], "payload.second.name", "β", "2/2")
                self.assertIn("@ 2 │", host.frames[5])
                self.assertIn(f"[JSON expansion skipped: {reason}]", host.frames[5])
                self.assertNotIn('"name": "β"', host.frames[5])


if __name__ == "__main__":
    unittest.main()
