"""Character focus, structural folding, and their search/input boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._input import parse_jsonl
from jsonl_viewer._model import FoldIdentity, ViewState
from jsonl_viewer._render import _text_cells, render_frame, strip_ansi
from jsonl_viewer.engine import _transition
from tests.support import FakeHost, styled_text, without_caret


def _source(*values: object) -> bytes:
    return "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values).encode()


class _View:
    """Mirror the engine's render/transition boundary without terminal ownership."""

    def __init__(self, source: bytes, *, size=(120, 40), color=False):
        self.snapshot = parse_jsonl(source)
        self.spec = ViewerSpec("session", "conversation", "agent", input_protocol="keys")
        self.state = ViewState()
        self.size = size
        self.color = color

    def render(self):
        result = render_frame(self.spec, self.state, snapshot=self.snapshot,
                              diagnostic=None, columns=self.size[0], rows=self.size[1],
                              color=self.color)
        self.state = replace(self.state, cursor=result.cursor if result.characters else self.state.cursor,
                             record_line_offset=result.record_line_offset,
                             horizontal_offset=result.horizontal_offset,
                             reveal_cursor=False, focus_container=None)
        return result

    def step(self, event):
        result = self.render()
        self.state, closed = _transition(self.state, event, self.snapshot, self.spec,
                                         page_size=result.body_rows,
                                         selected_line_count=result.selected_line_count,
                                         rendered=result)
        if closed:
            raise AssertionError("scenario unexpectedly closed the viewer")
        return self.render()

    def focus(self, character):
        self.state = replace(self.state, cursor=character.position, preferred_column=None,
                             reveal_match=False, reveal_cursor=True, focus_container=None)
        return self.render()

    def opener(self, path=(), record=0):
        identity = FoldIdentity(record, path)
        return next(cell for cell in self.render().characters
                    if cell.container == identity and cell.delimiter == "open")

    def toggle(self, path=(), record=0):
        self.focus(self.opener(path, record))
        return self.step("toggle_fold")


class CursorTests(unittest.TestCase):
    def test_initial_character_is_readable_inverse_or_plain_caret(self):
        source = _source({"content": {"value": 1}})
        frames = []
        for color in (False, True):
            view = _View(source, color=color)
            result = view.render()
            self.assertEqual(result.cursor, result.characters[0].position)
            cell = result.characters[0]
            self.assertEqual(cell.text, "{")
            lines = result.text.splitlines()
            if color:
                self.assertEqual(styled_text(result.text, "7"), "{}")
                self.assertEqual(styled_text(result.text, "4"), "{")
                self.assertNotIn("^", strip_ansi(result.text))
            else:
                self.assertEqual(lines[cell.screen_row + 1], " " * cell.screen_column + "^")
                self.assertNotIn("\x1b", result.text)
            frames.append(without_caret(strip_ansi(result.text)))
        self.assertEqual(*frames)

    def test_horizontal_movement_wraps_and_skips_gutters_and_expansion_cues(self):
        view = _View(_source({"content": json.dumps({"value": 1})}))
        opener = view.opener(("content",))
        view.focus(opener)
        result = view.step("cursor_left")
        previous = next(cell for cell in result.characters if cell.position == result.cursor)
        self.assertEqual(previous.text, " ")
        self.assertGreater(opener.position.column - previous.position.column, 1)
        self.assertEqual(view.step("cursor_right").cursor, opener.position)
        following = result.characters[next(index for index, cell in enumerate(result.characters) if cell.position == opener.position) + 1]
        self.assertEqual(view.step("cursor_right").cursor, following.position)
        self.assertEqual(view.step("cursor_left").cursor, opener.position)
        row = [cell for cell in result.characters
               if cell.position.line_index == opener.position.line_index]
        view.focus(row[0])
        preceding = result.characters[result.characters.index(row[0]) - 1]
        self.assertEqual(view.step("cursor_left").cursor, preceding.position)
        self.assertEqual(view.step("cursor_right").cursor, row[0].position)
        self.assertEqual(row[0].text, '"')
        self.assertFalse(any(cell.text in {"│", "…"} for cell in result.characters))

    def test_horizontal_wrap_preserves_visible_json_cells_and_viewport(self):
        source = _source({"content": json.dumps({"items": ["界e\u0301", {"x": 1}], "tail": 2}, ensure_ascii=False)}, "次e\u0301")
        for color in (False, True):
            for folded, size in ((False, (120, 40)), (True, (120, 40)), (False, (24, 12)), (True, (24, 12))):
                with self.subTest(color=color, folded=folded, size=size):
                    view = _View(source, color=color)
                    if folded:
                        view.toggle(("content", "items"))
                    view.size = size
                    result = view.render()
                    reference = _View(source, size=(240, 200), color=color)
                    reference.state = replace(view.state, horizontal_offset=0, cursor=None,
                                              record_line_offset=0)
                    projected = reference.render().characters
                    before = (view.state.selected_index, view.state.record_line_offset, view.state.folds)
                    for left, right in zip(result.characters, result.characters[1:]):
                        if left.screen_row == right.screen_row:
                            continue
                        left = [cell for cell in projected if
                                (cell.position.record_index, cell.position.line_index) ==
                                (left.position.record_index, left.position.line_index)][-1]
                        right = next(cell for cell in projected if
                                     (cell.position.record_index, cell.position.line_index) ==
                                     (right.position.record_index, right.position.line_index))
                        view.focus(left)
                        self.assertEqual(view.step("cursor_right").cursor, right.position)
                        self.assertEqual(view.state.preferred_column, right.position.column)
                        self.assertEqual(view.step("cursor_left").cursor, left.position)
                        self.assertEqual(view.state.preferred_column, left.position.column)
                    self.assertEqual((view.state.selected_index, view.state.record_line_offset, view.state.folds), before)
                    self.assertEqual(view.snapshot, parse_jsonl(source))

    def test_vertical_movement_preserves_preferred_display_column(self):
        view = _View(_source({"content": {"long": "abcdefghijk", "x": 0,
                                          "last": "abcdefghijk"}}))
        result = view.render()
        first = [cell for cell in result.characters if cell.position.line_index == 2][-2]
        view.focus(first)
        shorter = view.step("cursor_down")
        self.assertLess(shorter.cursor.column, first.position.column)
        restored = view.step("cursor_down")
        self.assertEqual(restored.cursor.column, first.position.column)
        self.assertEqual(restored.cursor.line_index, first.position.line_index + 2)
        self.assertEqual(view.state.preferred_column, first.position.column)
        view.step("cursor_left")
        self.assertEqual(view.state.preferred_column, view.state.cursor.column)

    def test_vertical_movement_skips_caret_and_record_separator_rows(self):
        view = _View(_source(1, 2, 3))
        first = view.render().cursor
        second = view.step("cursor_down").cursor
        self.assertEqual(second.record_index, 1)
        self.assertEqual(view.state.selected_index, 0)
        self.assertEqual(view.step("cursor_up").cursor, first)
        third = view.step("cursor_up").cursor
        self.assertEqual(third.record_index, 2)
        self.assertEqual(view.step("cursor_down").cursor, first)
        self.assertEqual(view.step("cursor_down").cursor, second)
        self.assertEqual(view.step("cursor_down").cursor, third)
        self.assertEqual(view.step("cursor_down").cursor, first)

    def test_successful_viewport_and_mode_changes_reset_to_directional_visible_row(self):
        source = _source(*({"content": list(range(20)), "extra": index} for index in range(3)))
        for event, setup in (("page_down", ()), ("page_up", ("page_down",)),
                             ("down", ()), ("up", ("down",)),
                             ("goto\t3", ()), ("toggle_mode", ())):
            with self.subTest(event=event):
                view = _View(source, size=(90, 12))
                for command in setup:
                    view.step(command)
                view.focus(view.render().characters[-1])
                result = view.step(event)
                if event == "page_up":
                    self.assertEqual(result.cursor.line_index, result.characters[-1].position.line_index)
                else:
                    self.assertEqual(result.cursor, result.characters[0].position)
                self.assertIsNone(view.state.preferred_column)

    def test_resize_clamps_cursor_and_plain_caret_fits_smallest_viewport(self):
        source = _source({"content": ["界e\u0301" * 30 for _ in range(20)]})
        for color in (False, True):
            for size in ((12, 4), (28, 6), (80, 12)):
                with self.subTest(color=color, size=size):
                    view = _View(source, color=color)
                    view.focus(view.render().characters[-1])
                    view.size = size
                    result = view.render()
                    self.assertIn(result.cursor, [cell.position for cell in result.characters])
                    lines = strip_ansi(result.text).splitlines()
                    self.assertLessEqual(len(lines), size[1])
                    self.assertTrue(all(_text_cells(line) <= size[0] for line in lines))
                    if not color:
                        cell = next(cell for cell in result.characters if cell.position == result.cursor)
                        self.assertEqual(lines[cell.screen_row + 1], " " * cell.screen_column + "^")
                        self.assertEqual(sum(line.strip() == "^" for line in lines), 1)
                        if size == (12, 4):
                            self.assertEqual(len(lines), 4)

    def test_wide_combining_and_escaped_controls_have_safe_character_positions(self):
        source = _source({"content": "界e\u0301😀\x1b[2J\u202ehidden"})
        view = _View(source, color=True)
        result = view.render()
        wide = next(cell for cell in result.characters if cell.text == "界")
        view.focus(wide)
        combined = view.step("cursor_right")
        cell = next(cell for cell in combined.characters if cell.position == combined.cursor)
        self.assertEqual(cell.text, "e\u0301")
        self.assertEqual(cell.position.column, wide.position.column + 2)
        self.assertEqual(styled_text(combined.text, "7"), "{e\u0301}")
        emoji = view.step("cursor_right")
        self.assertEqual(emoji.cursor.column, cell.position.column + 1)
        plain = strip_ansi(emoji.text)
        self.assertIn(r"\u001b[2J\u202e", plain)
        self.assertNotIn("\x1b", plain)
        self.assertNotIn("\u202e", plain)
        self.assertFalse(any(cell.text == "\u0301" for cell in result.characters))


class FoldingTests(unittest.TestCase):
    def test_innermost_delimiters_are_inverse_and_string_punctuation_is_ordinary(self):
        view = _View(_source({"content": {"items": ["{}[]"]}}), color=True)
        result = view.render()
        literal = next(cell for cell in result.characters if cell.text == "{" and cell.delimiter is None)
        result = view.focus(literal)
        self.assertEqual(literal.container, FoldIdentity(0, ("content", "items")))
        data = "\n".join(line.split("│", 1)[1] for line in result.text.splitlines() if "│" in line)
        self.assertEqual(styled_text(data, "1"), "[]")
        self.assertEqual(styled_text(data, "7"), "[{]")
        self.assertEqual(styled_text(data, "4"), "")
        folded = view.step("toggle_fold")
        self.assertIn('"items": [...]', strip_ansi(folded.text))

    def test_offscreen_matching_delimiter_does_not_scroll_into_view(self):
        view = _View(_source({"content": list(range(30))}), size=(80, 9), color=True)
        result = view.focus(view.opener(("content",)))
        before = result.record_line_offset
        data = "\n".join(line.split("│", 1)[1] for line in result.text.splitlines() if "│" in line)
        self.assertEqual(styled_text(data, "1"), "[")
        self.assertEqual(view.render().record_line_offset, before)
        self.assertFalse(any(cell.container == FoldIdentity(0, ("content",))
                             and cell.delimiter == "close" for cell in result.characters))

    def test_folded_shape_keeps_keys_commas_and_opening_focus(self):
        view = _View(_source({"content": {"items": [1, 2], "object": {"a": 1}, "last": 3}}))
        for path, expected in ((("content", "items"), '"items": [...],'),
                               (("content", "object"), '"object": {...},')):
            result = view.toggle(path)
            self.assertIn(expected, result.text)
            focused = next(cell for cell in result.characters if cell.position == result.cursor)
            self.assertEqual(focused.container, FoldIdentity(0, path))
            self.assertEqual(focused.delimiter, "open")
        self.assertIn('"last": 3', result.text)

    def test_fold_dots_keep_contrast_and_cursor_skips_to_highlighted_closer(self):
        source = _source({"content": {"array": [1], "object": {"x": 1}}})
        for color in (False, True):
            for name, pair in (("array", "[]"), ("object", "{}")):
                with self.subTest(color=color, container=name):
                    view = _View(source, color=color)
                    result = view.toggle(("content", name))
                    token = pair[0] + "..." + pair[1]
                    self.assertIn(token, strip_ansi(result.text))
                    self.assertFalse(any(cell.text == "." for cell in result.characters))
                    opening = result.cursor
                    if color:
                        self.assertEqual(styled_text(result.text, "7"), pair)
                        self.assertEqual(styled_text(result.text, "4"), pair[0])
                        self.assertNotIn("...", styled_text(result.text, "2"))
                        self.assertEqual(result.text.count("\x1b[0m.\x1b[0m"), 3)
                    result = view.step("cursor_right")
                    self.assertEqual(result.cursor.line_index, opening.line_index)
                    self.assertEqual(result.cursor.column, opening.column + 4)
                    if color:
                        self.assertEqual(styled_text(result.text, "7"), pair)
                        self.assertEqual(styled_text(result.text, "4"), pair[1])
                    else:
                        cell = next(cell for cell in result.characters
                                    if cell.position == result.cursor)
                        self.assertEqual(result.text.splitlines()[cell.screen_row + 1],
                                         " " * cell.screen_column + "^")
                    self.assertEqual(view.step("cursor_left").cursor, opening)
                    for size in ((12, 4), (16, 8), (24, 12), (80, 24)):
                        view.size = size
                        frame = strip_ansi(view.render().text).splitlines()
                        self.assertLessEqual(len(frame), size[1])
                        self.assertTrue(all(_text_cells(line) <= size[0] for line in frame))

    def test_nested_folds_survive_parent_reopen_and_mode_changes(self):
        view = _View(_source({"content": {"child": [1, 2], "tail": 3}, "hidden": 4}))
        child = FoldIdentity(0, ("content", "child"))
        parent = FoldIdentity(0, ("content",))
        view.toggle(child.path)
        view.toggle(parent.path)
        self.assertEqual(view.state.folds, frozenset({child, parent}))
        result = view.toggle(parent.path)
        self.assertIn('"child": [...],', result.text)
        self.assertEqual(view.state.folds, frozenset({child}))
        self.assertIn('"child": [...],', view.step("toggle_mode").text)
        self.assertIn('"child": [...],', view.step("toggle_mode").text)

    def test_fold_paths_are_per_record_and_can_target_a_later_visible_record(self):
        source = _source({"content": [1]}, {"content": [2]})
        view = _View(source)
        view.toggle(("content",), record=1)
        self.assertEqual(view.state.folds, frozenset({FoldIdentity(1, ("content",))}))
        self.assertEqual(view.state.selected_index, 0)
        self.assertIn("1", view.render().text)
        result = view.step("down")
        self.assertIn('"content": [...]', result.text)
        view.step("up")
        self.assertFalse(next(item for item in view.render().containers
                              if item.identity == FoldIdentity(0, ("content",))).folded)

    def test_encoded_containers_fold_at_structural_paths_and_retain_expansion_cues(self):
        value = {"content": json.dumps({"child": json.dumps([{"answer": 42}])})}
        source = _source(value)
        digest = hashlib.sha256(source).digest()
        view = _View(source)
        result = view.toggle(("content", "child"))
        self.assertIn('"child": [expanded JSON string ×1] [...]', result.text)
        self.assertIn("JSON display 2 expanded", result.text)
        result = view.toggle(("content",))
        self.assertIn('"content": [expanded JSON string ×1] {...}', result.text)
        result = view.toggle(("content",))
        self.assertIn('"child": [expanded JSON string ×1] [...]', result.text)
        self.assertEqual(view.snapshot.records[0].value, value)
        self.assertEqual(hashlib.sha256(source).digest(), digest)

    def test_empty_containers_and_scalar_roots_do_not_fold(self):
        for value in ({}, [], 1, True, None, "scalar", "{}", "[]"):
            with self.subTest(value=value):
                view = _View(_source(value))
                before = view.render()
                after = view.step("toggle_fold")
                self.assertEqual(after.text, before.text)
                self.assertEqual(view.state.folds, frozenset())

    def test_fold_identity_is_immutable_and_session_state_is_not_reused(self):
        identity = FoldIdentity(0, ("content",))
        with self.assertRaises(FrozenInstanceError):
            identity.record_index = 1
        source = _source({"content": [1, 2]})
        first, second = FakeHost(("toggle_fold", "close")), FakeHost(("close",))
        view_jsonl(source, ViewerSpec("s", "c", "a"), first)
        view_jsonl(source, ViewerSpec("s", "c", "a"), second)
        self.assertIn("{...}", first.frames[-1])
        self.assertNotIn("{...}", second.frames[-1])
        self.assertEqual((first.close_calls, second.close_calls), (1, 1))


class CursorSearchAndInputTests(unittest.TestCase):
    def test_help_body_documents_cursor_fold_and_record_bindings(self):
        bodies = []
        for event in ("text\t?", "help"):
            with self.subTest(event=event):
                view = _View(_source({"content": [1, 2]}))
                result = view.step(event)
                self.assertTrue(view.state.help_visible)
                # Exclude the footer: it already documents these commands and
                # must not hide stale instructions in the actual help body.
                body = "\n".join(strip_ansi(result.text).splitlines()[1:-2])
                self.assertRegex(body, r"h/l[^\n]*cursor[^\n]*left/right[^\n]*reveal full rows")
                self.assertRegex(body, r"←/→[^\n]*pan[^\n]*half[^\n]*left/right")
                self.assertRegex(body, r"j/k[^\n]*cursor[^\n]*down/up")
                self.assertRegex(body, r"Enter[^\n]*(?:fold|collapse|expand)")
                self.assertRegex(body, r"↑/↓[^\n]*record")
                self.assertRegex(body, r"\?[^\n]*help")
                self.assertNotIn("↑/↓ or j/k", body)
                self.assertNotRegex(body, r"j/k[^\n]*source record")
                bodies.append(body)
        self.assertEqual(*bodies)

    def test_search_unfolds_only_hit_ancestors_and_preserves_occurrence_counts(self):
        source = _source({"content": {"left": ["needle needle"], "right": ["other"]}},
                         {"content": {"left": ["needle"]}})
        view = _View(source)
        for path in (("content", "left"), ("content", "right"), ("content",), ()):
            view.toggle(path)
        result = view.step("search\tneedle")
        self.assertEqual(view.state.folds, frozenset({FoldIdentity(0, ("content", "right"))}))
        self.assertEqual(len(view.state.search.occurrences), 3)
        self.assertIn("1/3 occurrences", result.text)
        cell = next(cell for cell in result.characters if cell.position == result.cursor)
        self.assertEqual(cell.text, "n")
        self.assertTrue(cell.search_focus)
        for event, ordinal in (("next_match", 2), ("next_match", 3), ("next_match", 1),
                               ("previous_match", 3)):
            result = view.step(event)
            self.assertIn(f"{ordinal}/3 occurrences", result.text)
            self.assertEqual(len(view.state.search.occurrences), 3)
            self.assertTrue(next(cell for cell in result.characters
                                 if cell.position == result.cursor).search_focus)

    def test_manually_folded_active_hit_stays_folded_until_search_navigation(self):
        view = _View(_source({"content": {"items": ["needle", "needle"]}}))
        view.step("search\tneedle")
        result = view.step("toggle_fold")
        identity = FoldIdentity(0, ("content", "items"))
        self.assertIn(identity, view.state.folds)
        self.assertIn('"items": [...]', result.text)
        self.assertNotIn("⟦needle⟧", result.text)
        self.assertIn("1/2 occurrences", result.text)
        for event in ("cursor_right", "cursor_left", "unknown"):
            result = view.step(event)
            self.assertIn(identity, view.state.folds)
            self.assertIn('"items": [...]', result.text)
        view.size = (80, 14)
        self.assertIn('"items": [...]', view.render().text)
        result = view.step("next_match")
        self.assertNotIn(identity, view.state.folds)
        self.assertIn("2/2 occurrences", result.text)
        self.assertIn("⟦needle⟧", result.text)

    def test_search_key_of_folded_container_does_not_needlessly_open_its_value(self):
        view = _View(_source({"content": {"needle": [1, 2]}}))
        view.toggle(("content", "needle"))
        view.toggle(("content",))
        result = view.step("search\tneedle")
        self.assertEqual(view.state.folds, frozenset({FoldIdentity(0, ("content", "needle"))}))
        self.assertIn('"⟦needle⟧": [...]', result.text)

    def test_encoded_search_reveal_and_cursor_inverse_share_readable_first_character(self):
        source = _source({"content": json.dumps({"items": ["界e\u0301 needle"]}, ensure_ascii=False)})
        view = _View(source, color=True)
        view.toggle(("content", "items"))
        result = view.step("search\t界e\u0301")
        self.assertIn("⟦界e\u0301⟧", strip_ansi(result.text))
        self.assertEqual(styled_text(result.text, "7"), "[界]")
        self.assertIn("界e\u0301", styled_text(result.text, "43"))
        cell = next(cell for cell in result.characters if cell.position == result.cursor)
        self.assertEqual(cell.text, "界")
        self.assertTrue(cell.search_focus)
        self.assertEqual(view.state.folds, frozenset())

    def test_leading_combining_search_hits_keep_readable_focus_and_original_occurrences(self):
        mark = "\u0301"
        value = {"content": {mark: mark + "base"}}
        source = _source(value)
        digest = hashlib.sha256(source).digest()
        for color in (False, True):
            view = _View(source, color=color)
            for event, ordinal, count, kind, escaped in (
                ("search\t" + mark, 1, 2, "key", r"\u0301"),
                ("next_match", 2, 2, "value", r"\u0301"),
                ("previous_match", 1, 2, "key", r"\u0301"),
                ("search\t" + mark + "base", 1, 1, "value", r"\u0301base"),
            ):
                with self.subTest(color=color, event=event):
                    result = view.step(event)
                    plain = strip_ansi(result.text)
                    self.assertIn("⟦" + escaped + "⟧", plain)
                    self.assertNotIn("⟦" + mark, plain)
                    self.assertIn(f"{ordinal}/{count} occurrences", plain)
                    search = view.state.search
                    self.assertEqual(len(search.occurrences), count)
                    hit = search.current_occurrence
                    self.assertEqual((hit.path, hit.kind, hit.start, hit.end),
                                     (("content", mark), kind, 0, len(search.query)))
                    self.assertEqual(hit.text, mark if kind == "key" else mark + "base")
                    focused = next(cell for cell in result.characters
                                   if cell.position == result.cursor)
                    self.assertTrue(focused.search_focus)
                    self.assertEqual(focused.text, "\\")
                    self.assertEqual(_text_cells(focused.text), 1)
                    if color:
                        self.assertEqual(styled_text(result.text, "7"), "{\\}")
                        self.assertIn(escaped, styled_text(result.text, "43"))
                    else:
                        self.assertEqual(plain.splitlines()[focused.screen_row + 1],
                                         " " * focused.screen_column + "^")
                    self.assertEqual(view.snapshot.records[0].value, value)
                    self.assertEqual(hashlib.sha256(source).digest(), digest)

    def test_main_keys_and_line_commands_match_semantic_events(self):
        source = _source({"content": [1, 2]}, {"content": [3, 4]})
        pairs = (("text\tj", "cursor_down"), ("text\tk", "cursor_up"),
                 ("text\th", "cursor_left"), ("text\tl", "cursor_right"),
                 ("key\tenter", "toggle_fold"), ("key\tdown", "down"),
                 ("key\tup", "up"), ("text\t?", "help"),
                 ("line\t h ", "cursor_left"), ("line\tl", "cursor_right"),
                 ("line\tj", "cursor_down"), ("line\tk", "cursor_up"),
                 ("line\tfold", "toggle_fold"), ("line\t?", "help"),
                 ("line\thelp", "help"), ("text\t ", "page_down"),
                 ("text\tb", "page_up"), ("key\tpage_down", "page_down"),
                 ("key\tpage_up", "page_up"))
        for key, semantic in pairs:
            with self.subTest(key=key):
                physical, legacy = _View(source), _View(source)
                physical.step("cursor_down")
                legacy.step("cursor_down")
                self.assertEqual(physical.step(key).text, legacy.step(semantic).text)
                self.assertEqual(physical.state, legacy.state)

    def test_new_main_keys_remain_literal_and_enter_submits_prompt(self):
        view = _View(_source({"content": "hjkl fold ?"}))
        view.step("text\t/")
        view.step("text\thjkl fold ?")
        self.assertEqual(view.state.prompt.buffer, "hjkl fold ?")
        self.assertFalse(view.state.help_visible)
        self.assertEqual(view.state.folds, frozenset())
        result = view.step("key\tenter")
        self.assertIsNone(view.state.prompt)
        self.assertEqual(view.state.search.query, "hjkl fold ?")
        self.assertIn("1/1 occurrences", result.text)
        self.assertEqual(view.state.folds, frozenset())

    def test_batched_main_text_matches_individual_cursor_events(self):
        source = _source({"content": {"items": [1, 2]}})
        batched, separate = _View(source), _View(source)
        result = batched.step("text\tjjllkh")
        for character in "jjllkh":
            expected = separate.step("text\t" + character)
        self.assertEqual(result.text, expected.text)
        self.assertEqual(batched.state, separate.state)

    def test_smallest_prompt_keeps_editor_hint_error_and_cancelled_cursor(self):
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(_source({"content": [1, 2]}), size=(12, 4), color=color)
                before = view.render()
                editor = strip_ansi(view.step("text\tg").text)
                self.assertIn("Line: ", editor)
                self.assertIn("Enter subm", editor)
                view.step("text\t0")
                failed = strip_ansi(view.step("key\tenter").text)
                self.assertIsNotNone(view.state.prompt.error)
                self.assertIn("Line: 0", failed)
                self.assertTrue(any(line.startswith(view.state.prompt.error[:8])
                                    for line in failed.splitlines()))
                restored = view.step("key\tescape")
                self.assertEqual(restored.cursor, before.cursor)
                self.assertEqual(restored.text, before.text)


if __name__ == "__main__":
    unittest.main()
