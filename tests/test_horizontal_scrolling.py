"""Horizontal navigation reveals retained JSON without moving terminal chrome."""

from __future__ import annotations

import hashlib
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._model import FoldIdentity
from jsonl_viewer._render import _text_cells, strip_ansi
from tests.support import FakeHost, styled_text, without_caret
from tests.test_blink_and_siblings import _ObservedHost
from tests.test_cursor_and_folding import _View, _source


def _body(frame):
    return [line for line in strip_ansi(frame).splitlines() if " │ " in line]


def _focused(result):
    return next(cell for cell in result.characters if cell.position == result.cursor)


def _host_cursor_glyph(frame):
    """Read painted focus without rendering again or inspecting engine state."""
    if "\x1b" in frame:
        return styled_text(frame, "7").strip("{}[]") or None
    lines = frame.splitlines()
    for row, line in enumerate(lines):
        if line.strip() == "^":
            column = line.index("^")
            return next((character for index, character in enumerate(lines[row - 1])
                         if _text_cells(lines[row - 1][:index]) == column), None)
    return None


class HorizontalScrollingTests(unittest.TestCase):
    def test_public_loop_reveals_wide_cells_in_two_column_body_and_retains_idle_focus(self):
        cases = (("界界界", ("goto\t10000",)),
                 ({"value": "界界界"},
                  ("toggle_mode", "goto\t10000", "cursor_down") + ("text\tl",) * 9))
        for value, setup in cases:
            source = b"0\n" * 9999 + _source(value)
            for color in (False, True):
                with self.subTest(value=value, color=color):
                    events = (*setup, "text\tl", "idle", "idle", "clear_search", "text\tl", "text\th", "close")
                    host = _ObservedHost(events, size=(12, 8), color=color)
                    view_jsonl(source, ViewerSpec("s", "c", "a", input_protocol="keys"), host)
                    target = len(setup) + 1
                    self.assertEqual(_host_cursor_glyph(host.observed[target - 1]), '"')
                    for index in (target, target + 2, target + 3, target + 4, target + 5):
                        frame = host.observed[index]
                        self.assertEqual(_host_cursor_glyph(frame), "界")
                        self.assertTrue(any(line.endswith("10000 │ 界") for line in _body(frame)))
                        self.assertTrue(all(_text_cells(line) <= 12 for line in strip_ansi(frame).splitlines()))
                    self.assertIsNone(_host_cursor_glyph(host.observed[target + 1]))
                    self.assertEqual(_body(host.observed[target]), _body(host.observed[target + 1]))
                    self.assertEqual(host.observed[target], host.observed[target + 2])
                    self.assertEqual(host.close_calls, 1)

    def test_public_loop_cursor_uses_neighbors_after_search_and_sibling_reveal(self):
        cases = (({"content": "x" * 100 + "needle" + "y" * 20},
                  ("search\tneedle",), (40, 12), "n", "e"),
                 ({"content": {"first": "x" * 100 + "needle" + "y" * 20, "second": 1}},
                  ("search\tneedle", "next_sibling"), (12, 8), "s", "e"))
        for value, setup, size, first, following in cases:
            source = _source(value)
            for color in (False, True):
                with self.subTest(setup=setup, color=color):
                    host = _ObservedHost((*setup, "text\tl", "text\th", "idle", "idle", "close"),
                                         size=size, color=color)
                    view_jsonl(source, ViewerSpec("s", "c", "a", input_protocol="keys"), host)
                    target = len(setup)
                    self.assertEqual(_host_cursor_glyph(host.observed[target]), first)
                    self.assertEqual(_host_cursor_glyph(host.observed[target + 1]), following)
                    self.assertEqual(_host_cursor_glyph(host.observed[target + 2]), first)
                    self.assertEqual(_host_cursor_glyph(host.observed[target + 4]), first)
                    self.assertEqual(host.close_calls, 1)
                    self.assertEqual(source, _source(value))

    def test_arrows_pan_half_body_clamp_and_keep_gutters_and_chrome_fixed(self):
        view = _View(_source({"content": "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 5}), size=(40, 12))
        initial = view.render()
        step = max(1, initial.body_width // 2)
        target = next(cell for cell in initial.characters if cell.position.column == 30)
        view.focus(target)
        first = view.step("scroll_right")
        self.assertEqual(first.horizontal_offset, step)
        self.assertEqual(first.cursor, target.position)
        self.assertEqual(_focused(first).screen_column, target.position.column - step)
        second = view.step("scroll_right")
        self.assertEqual(second.horizontal_offset, step * 2)
        self.assertNotEqual(second.cursor, target.position)
        self.assertIn(second.cursor, [cell.position for cell in second.characters])
        self.assertEqual(initial.text.splitlines()[:2], second.text.splitlines()[:2])
        self.assertEqual([line.split("│")[0] for line in _body(initial.text)],
                         [line.split("│")[0] for line in _body(second.text)])
        self.assertIn(f"x {step * 2} ←→", second.text)
        for _ in range(20):
            last = view.step("scroll_right")
        self.assertEqual(last.horizontal_offset, last.max_horizontal_offset)
        self.assertIn(f"x {last.horizontal_offset} ← •", last.text)
        self.assertIn('"', _body(last.text)[1])
        self.assertEqual(view.step("scroll_right").text, last.text)
        previous = view.step("scroll_left")
        self.assertEqual(previous.horizontal_offset, max(0, last.horizontal_offset - step))
        for _ in range(20):
            first = view.step("scroll_left")
        self.assertEqual(first.horizontal_offset, 0)
        self.assertIn("x 0 →", first.text)
        self.assertEqual(view.step("scroll_left").text, first.text)

    def test_character_cursor_reveals_hidden_suffix_before_wrapping_actual_row(self):
        source = _source({"content": "BEGIN" + "x" * 70 + "TAIL"})
        view = _View(source, size=(24, 12))
        visible = [cell for cell in view.render().characters if cell.position.line_index == 1]
        edge = visible[-1]
        view.focus(edge)
        result = view.step("cursor_right")
        self.assertEqual(result.cursor.line_index, 1)
        self.assertEqual(result.cursor.column, edge.position.column + 1)
        if result.horizontal_offset == 0:
            edge = _focused(result)
            self.assertEqual(edge.screen_column, view.size[0] - 1)
            result = view.step("cursor_right")
            self.assertEqual(result.cursor.line_index, 1)
            self.assertEqual(result.cursor.column, edge.position.column + 1)
        self.assertGreater(result.horizontal_offset, 0)
        seen = []
        for _ in range(120):
            focused = _focused(result)
            if result.cursor.line_index != 1:
                break
            seen.append(focused.text)
            last = focused.position
            result = view.step("cursor_right")
        else:
            self.fail("cursor did not reach the retained row boundary")
        self.assertTrue("".join(seen).endswith('TAIL"'))
        self.assertEqual(_focused(result).text, "}")
        self.assertEqual(result.cursor.line_index, 2)
        back = view.step("cursor_left")
        self.assertEqual(back.cursor, last)
        self.assertEqual(_focused(back).text, '"')
        self.assertGreater(back.horizontal_offset, 0)
        self.assertEqual(view.snapshot.records[0].value["content"], "BEGIN" + "x" * 70 + "TAIL")

    def test_unicode_pan_keeps_whole_clusters_and_aligned_plain_caret(self):
        source = _source("界e\u0301😀" * 16)
        frames = []
        for color in (False, True):
            view = _View(source, size=(13, 12), color=color)
            for _ in range(8):
                result = view.step("scroll_right")
                plain = strip_ansi(result.text)
                self.assertTrue(all(_text_cells(line) <= 13 for line in plain.splitlines()))
                self.assertTrue(all(cell.text in {'"', "界", "e\u0301", "😀"}
                                    for cell in result.characters))
                self.assertFalse(any(cell.text.startswith("\u0301") for cell in result.characters))
                focused = _focused(result)
                self.assertGreaterEqual(focused.screen_column, 6)
                if not color:
                    self.assertEqual(plain.splitlines()[focused.screen_row + 1],
                                     " " * focused.screen_column + "^")
            frames.append(without_caret(strip_ansi(result.text)))
        self.assertEqual(*frames)

    def test_record_goto_and_mode_reset_pan_but_same_record_pages_preserve_it(self):
        source = _source({"content": ["abcdefghijklmnop" * 8] * 12},
                         {"content": ["abcdefghijklmnop" * 8] * 12})
        for event in ("down", "up", "goto\t2", "toggle_mode"):
            view = _View(source, size=(40, 9))
            before = view.step("scroll_right")
            self.assertGreater(before.horizontal_offset, 0)
            after = view.step(event)
            self.assertEqual(after.horizontal_offset, 0)
            self.assertEqual(after.cursor, after.characters[0].position)
        view = _View(source, size=(40, 9))
        before = view.step("scroll_right")
        for event in ("page_down", "page_down", "page_up"):
            after = view.step(event)
            self.assertEqual(view.state.selected_index, 0)
            self.assertEqual(after.horizontal_offset, before.horizontal_offset)
        view.size = (240, 20)
        widened = view.render()
        self.assertEqual(widened.horizontal_offset, 0)
        self.assertEqual(widened.max_horizontal_offset, 0)

    def test_offset_clamps_to_current_vertical_viewport_and_visible_record_widths(self):
        view = _View(_source({"content": ["x" * 100] * 4 + [1] * 20}), size=(40, 8))
        view.step("scroll_right")
        for _ in range(4):
            result = view.step("page_down")
        self.assertGreater(view.state.record_line_offset, 6)
        self.assertEqual(view.state.selected_index, 0)
        self.assertEqual(result.max_horizontal_offset, 0)
        self.assertEqual(result.horizontal_offset, 0)
        view = _View(_source(0, "x" * 100), size=(40, 12))
        result = view.step("scroll_right")
        self.assertEqual(view.state.selected_index, 0)
        self.assertGreater(result.horizontal_offset, 0)
        self.assertEqual(result.cursor.record_index, 1)

    def test_physical_semantic_and_ordinary_line_arrows_render_equivalent_frames(self):
        source = _source({"content": "0123456789" * 16})
        scripts = (("scroll_right", "scroll_right", "scroll_left"),
                   ("key\tright", "key\tright", "key\tleft"),
                   ("line\tright", "line\t right ", "line\tleft"))
        hosts = []
        for script in scripts:
            host = FakeHost((*script, "close"), size=(40, 12))
            view_jsonl(source, ViewerSpec("s", "c", "a", input_protocol="keys"), host)
            self.assertEqual(host.close_calls, 1)
            hosts.append(host)
        self.assertEqual(hosts[0].frames, hosts[1].frames)
        self.assertEqual(hosts[1].frames, hosts[2].frames)
        semantic = FakeHost((*scripts[0], "close"), size=(40, 12))
        view_jsonl(source, ViewerSpec("s", "c", "a"), semantic)
        self.assertEqual(semantic.frames, hosts[0].frames)

    def test_prompt_arrows_edit_drafts_and_cancel_restores_pan_while_help_is_stationary(self):
        view = _View(_source({"content": "needle " * 20}), size=(40, 12))
        before = view.step("scroll_right")
        for opener in ("text\t/", "text\tg"):
            view.step(opener)
            view.step("text\t123")
            view.step("key\tleft")
            self.assertEqual(view.state.prompt.cursor, 2)
            view.step("key\tright")
            self.assertEqual(view.state.prompt.cursor, 3)
            self.assertEqual(view.state.horizontal_offset, before.horizontal_offset)
            self.assertEqual(view.step("key\tescape").text, before.text)
        help_frame = view.step("help")
        for event in ("key\tright", "key\tleft", "scroll_right", "scroll_left"):
            self.assertEqual(view.step(event).text, help_frame.text)
        self.assertEqual(view.step("help").text, before.text)

    def test_batched_character_input_refreshes_scroll_geometry_after_each_action(self):
        source = _source({"content": ["0123456789" * 10, "界e\u0301" * 25], "extra": 0})
        batch, separate = _View(source, size=(24, 9)), _View(source, size=(24, 9))
        keys = "l" * 105 + "h" * 60 + "mjjllkh"
        actual = batch.step("text\t" + keys)
        for key in keys:
            expected = separate.step("text\t" + key)
        self.assertEqual(actual.text, expected.text)
        self.assertEqual(batch.state, separate.state)
        host = FakeHost(("text\t" + keys, "close"), size=batch.size)
        view_jsonl(source, batch.spec, host)
        self.assertEqual(host.frames[-1], actual.text)

    def test_fold_and_search_reveal_targets_and_keep_search_counts_and_source_bytes(self):
        source = _source({"content": ["x" * 80 + "needle needle"]})
        digest = hashlib.sha256(source).digest()
        view = _View(source, size=(40, 12))
        view.step("scroll_right")
        folded = view.step("toggle_fold")
        self.assertIn(FoldIdentity(0, ("content",)), view.state.folds)
        self.assertEqual(_focused(folded).text, "[")
        self.assertEqual(folded.horizontal_offset, 0)
        result = view.step("search\tneedle")
        self.assertEqual(view.state.folds, frozenset())
        self.assertIn("⟦needle⟧", result.text)
        self.assertIn("1/2 occurrences", result.text)
        count = len(view.state.search.occurrences)
        view.step("scroll_right")
        result = view.step("next_match")
        self.assertIn("⟦needle⟧", result.text)
        self.assertIn("2/2 occurrences", result.text)
        self.assertEqual(len(view.state.search.occurrences), count)
        self.assertEqual(hashlib.sha256(source).digest(), digest)
        self.assertEqual(view.snapshot.records[0].value["content"][0], "x" * 80 + "needle needle")

    def test_idle_reuses_panned_frame_and_reopen_discards_horizontal_state(self):
        source = _source({"content": "0123456789" * 20})
        host = _ObservedHost(("scroll_right", "idle", "idle", "close"), size=(40, 12))
        view_jsonl(source, ViewerSpec("s", "c", "a"), host)
        initial, on, off, again = host.observed
        self.assertIn("x 17 ←→", on)
        self.assertEqual(on, again)
        self.assertNotEqual(on, off)
        self.assertEqual(_body(on), _body(off))
        self.assertEqual(on.splitlines()[-2:], off.splitlines()[-2:])
        self.assertEqual(host.close_calls, 1)
        reopened = FakeHost(("close",), size=(40, 12))
        view_jsonl(source, ViewerSpec("s", "c", "a"), reopened)
        self.assertEqual(reopened.frames[-1], initial)
        self.assertEqual(reopened.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
