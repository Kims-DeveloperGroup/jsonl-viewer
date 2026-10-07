"""Mouse reports use viewer navigation and the last painted JSON cells."""

from __future__ import annotations

import unittest

from jsonl_viewer._mouse import decode_sgr, parse_mouse
from jsonl_viewer._render import strip_ansi
from tests.test_cursor_and_folding import _View, _source


def _mouse(button=0, column=1, row=1, phase="press"):
    return f"mouse\t{button}\t{column}\t{row}\t{phase}"


class MouseInputTests(unittest.TestCase):
    def test_sgr_reports_normalize_bounded_coordinates_and_phase(self):
        for body, expected in (
            ("0;12;8M", _mouse(0, 12, 8)),
            ("0;12;8m", _mouse(0, 12, 8, "release")),
            ("64;1;1M", _mouse(64)),
            ("65;999999;999999M", _mouse(65, 999999, 999999)),
        ):
            with self.subTest(body=body):
                self.assertEqual(decode_sgr(body), expected)
                self.assertIsNotNone(parse_mouse(expected))
        for body in ("", "0;0;1M", "0;1;0M", "0;1;1", "0;-1;1M",
                     "0;1000000;1M", "256;1;1M", "1;1;1Mextra", "0" * 10000):
            with self.subTest(body=body):
                self.assertEqual(decode_sgr(body), "mouse\tinvalid")

    def test_invalid_envelopes_are_inert_instead_of_command_errors(self):
        view = _View(_source({"content": "alpha"}))
        frame = view.render()
        for event in ("mouse\tinvalid", "mouse\t0\t0\t1\tpress",
                      "mouse\t0\t1\t0\tpress", "mouse\t0\t1\t1\tPRESS",
                      "mouse\t0\t1\t1\tpress\textra", "mouse\t" + "0" * 10000):
            with self.subTest(event=event[:80]):
                self.assertEqual(view.step(event).text, frame.text)
                self.assertIsNone(view.state.message)

    def test_wheel_matches_cursor_keys_across_pages_records_and_folds(self):
        source = _source({"content": {"fold": list(range(12)), "tail": "end"}},
                         {"content": list(range(20))}, {"content": "last"})
        for color in (False, True):
            with self.subTest(color=color):
                wheel = _View(source, size=(55, 10), color=color)
                keys = _View(source, size=(55, 10), color=color)
                for view in (wheel, keys):
                    view.toggle(("content", "fold"))
                for button, key in ((65, "text\tj"),) * 60 + ((64, "text\tk"),) * 60:
                    actual = wheel.step(_mouse(button, 5, 5))
                    expected = keys.step(key)
                    self.assertEqual(actual.text, expected.text)
                    self.assertEqual(wheel.state, keys.state)
                self.assertEqual(wheel.snapshot, keys.snapshot)

    def test_click_uses_painted_cells_in_plain_and_color_with_unicode(self):
        source = _source({"content": "A界e\u0301😀Z"}, {"content": 2})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, size=(100, 20), color=color)
                initial = view.render()
                for initial_target in initial.characters:
                    if initial_target.text not in ("界", "e\u0301", "😀", "Z"):
                        continue
                    for extra in range(initial_target.screen_width):
                        target = next(cell for cell in view.render().characters
                                      if cell.position == initial_target.position)
                        clicked = view.step(_mouse(column=target.screen_column + extra + 1,
                                                  row=target.screen_row + 1))
                        self.assertEqual(clicked.cursor, target.position)
                        focused = next(cell for cell in clicked.characters
                                       if cell.position == clicked.cursor)
                        self.assertEqual(focused.text, target.text)
                self.assertEqual(source, _source({"content": "A界e\u0301😀Z"}, {"content": 2}))

    def test_click_uses_visible_panned_character_not_logical_column(self):
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(_source({"content": "abcdefghijklmnop界e\u0301😀" * 5}),
                             size=(35, 12), color=color)
                panned = view.step("scroll_right")
                self.assertGreater(panned.horizontal_offset, 0)
                target = next(cell for cell in panned.characters if cell.text == "m")
                selected = view.step(_mouse(column=target.screen_column + 1,
                                            row=target.screen_row + 1))
                self.assertEqual(selected.cursor, target.position)
                self.assertEqual(selected.horizontal_offset, panned.horizontal_offset)

    def test_non_json_cells_and_non_navigation_reports_leave_view_unchanged(self):
        for color in (False, True):
            view = _View(_source({"content": "alpha"}), size=(70, 14), color=color)
            frame = view.render()
            body = frame.characters[0].screen_row + 1
            rows = strip_ansi(frame.text).splitlines()
            caret = next((index + 1 for index, line in enumerate(rows)
                          if line.strip() == "^"), None)
            events = [_mouse(column=1, row=1), _mouse(column=1, row=body),
                      _mouse(column=70, row=body), _mouse(column=71, row=body),
                      _mouse(column=1, row=15)]
            if caret is not None:
                events.append(_mouse(column=rows[caret - 1].index("^") + 1, row=caret))
            cell = frame.characters[-1]
            for button, phase in ((0, "release"), (32, "press"), (1, "press"),
                                  (2, "press"), (64, "release"), (65, "release")):
                events.append(_mouse(button, cell.screen_column + 1, cell.screen_row + 1, phase))
            for event in events:
                with self.subTest(color=color, event=event):
                    self.assertEqual(view.step(event).text, frame.text)

    def test_mouse_does_not_edit_drafts_or_dismiss_help(self):
        for setup in (("text\t/", "text\tabc界"), ("text\tg", "text\t12"), ("help",)):
            with self.subTest(setup=setup):
                view = _View(_source({"content": "alpha"}), size=(100, 20))
                for event in setup:
                    view.step(event)
                expected = view.state
                frame = view.render()
                for event in (_mouse(), _mouse(64), _mouse(65), "mouse\tinvalid"):
                    self.assertEqual(view.step(event).text, frame.text)
                    self.assertEqual(view.state, expected)


if __name__ == "__main__":
    unittest.main()
