"""Circular record selection remains separate from cursor and paging actions."""

from __future__ import annotations

import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._render import _text_cells, strip_ansi
from tests.test_blink_and_siblings import _ObservedHost
from tests.test_cursor_and_folding import _View, _source


class RecordNavigationTests(unittest.TestCase):
    def test_record_cycles_report_arrival_and_clear_notices_interior(self):
        view = _View(_source({'content': 0}, {'content': 1}, {'content': 2}))
        for event, index, notice in (
            ('up', 2, 'Last record.'), ('down', 0, 'First record.'),
            ('down', 1, None), ('down', 2, 'Last record.'),
            ('down', 0, 'First record.'), ('up', 2, 'Last record.'),
            ('up', 1, None), ('up', 0, 'First record.'),
        ):
            with self.subTest(event=event, index=index, notice=notice):
                result = view.step(event)
                self.assertEqual(view.state.selected_index, index)
                self.assertEqual(view.state.message, notice)
                self.assertFalse(view.state.message_is_error)
                self.assertEqual(result.cursor, result.characters[0].position)
                self.assertEqual(result.cursor.record_index, index)
                self.assertIsNone(view.state.preferred_column)
                if notice:
                    self.assertIn(notice, result.text)

    def test_singleton_resets_scrolled_viewport_and_cursor_each_time(self):
        for event in ('up', 'down', 'key\tup', 'key\tdown', 'line\tup', 'line\tdown'):
            with self.subTest(event=event):
                view = _View(_source({'content': list(range(40))}), size=(60, 12))
                view.step('page_down')
                self.assertGreater(view.state.record_line_offset, 0)
                view.focus(view.render().characters[-1])
                result = view.step(event)
                self.assertEqual(view.state.selected_index, 0)
                self.assertEqual(view.state.record_line_offset, 0)
                self.assertEqual(result.cursor, result.characters[0].position)
                self.assertEqual(result.cursor.line_index, 0)
                self.assertIsNone(view.state.preferred_column)
                self.assertEqual(view.state.message, 'Only record.')
                self.assertIn('Only record.', result.text)

    def test_wrapping_resets_cursor_but_preserves_record_folds_and_search(self):
        source = _source({'content': {'items': ['needle', 'first']}},
                         {'content': {'items': ['needle', 'last']}})
        view = _View(source, size=(48, 12))
        view.step('search\tneedle')
        view.toggle(('content', 'items'))
        search, folds = view.state.search, view.state.folds
        for event, index in (('up', 1), ('down', 0), ('down', 1), ('down', 0)):
            result = view.step(event)
            self.assertEqual(view.state.selected_index, index)
            self.assertEqual(view.state.search, search)
            self.assertEqual(view.state.folds, folds)
            self.assertFalse(view.state.reveal_match)
            self.assertEqual(result.cursor, result.characters[0].position)
            if index == 0:
                self.assertIn('"items": [...]', result.text)
        self.assertEqual(view.snapshot.records[0].value['content']['items'], ['needle', 'first'])

    def test_escape_dismisses_notice_before_active_search_and_idle_preserves_it(self):
        source = _source({'content': 'needle'}, {'content': 'needle'})
        view = _View(source)
        view.step('search\tneedle')
        search = view.state.search
        view.step('up')
        position = view.state.cursor
        view.step('idle')
        self.assertEqual(view.state.message, 'Last record.')
        self.assertEqual(view.state.cursor, position)
        view.size = (24, 9)
        self.assertIn('Last record.', view.render().text)
        view.step('key\tescape')
        self.assertIsNone(view.state.message)
        self.assertEqual(view.state.search, search)
        view.step('key\tescape')
        self.assertIsNone(view.state.search)
        self.assertEqual(view.state.message, 'Search cleared.')
        host = _ObservedHost(('up', 'idle', 'text\t/', 'close'))
        view_jsonl(source, ViewerSpec('s', 'c', 'a', input_protocol='keys'), host)
        self.assertIn('Last record.', host.observed[1])
        self.assertIn('Last record.', host.observed[2])
        self.assertIn('Search query:', host.observed[3])
        self.assertNotIn('Last record.', host.observed[3])

    def test_notice_is_non_error_after_invalid_goto_and_cursor_movement_clears_it(self):
        for event, count, notice in (('up', 2, 'Last record.'), ('down', 2, 'Last record.'),
                                     ('up', 1, 'Only record.')):
            view = _View(_source(*({'content': index} for index in range(count))), color=True)
            view.step('goto\t0')
            self.assertTrue(view.state.message_is_error)
            result = view.step(event)
            self.assertEqual(view.state.message, notice)
            self.assertFalse(view.state.message_is_error)
            self.assertIn(notice, strip_ansi(result.text))
            view.step('cursor_right')
            self.assertIsNone(view.state.message)

    def test_physical_line_and_legacy_events_wrap_but_jk_stay_character_navigation(self):
        for previous, following in (('up', 'down'), ('key\tup', 'key\tdown'),
                                    ('line\tup', 'line\tdown')):
            view = _View(_source(0, 1, 2))
            view.step(previous)
            self.assertEqual(view.state.selected_index, 2)
            view.step(following)
            self.assertEqual(view.state.selected_index, 0)
            view.step('text\tj')
            self.assertEqual(view.state.selected_index, 0)
            view.step('text\tk')
            self.assertEqual(view.state.selected_index, 0)

    def test_empty_and_narrow_ansi_plain_frames_remain_safe_and_bounded(self):
        for color in (False, True):
            for size in ((12, 4), (24, 9), (80, 20)):
                for source in (b'', _source({'content': 1}), _source(1, 2)):
                    with self.subTest(color=color, size=size, source=source):
                        view = _View(source, color=color, size=size)
                        for event in ('up', 'down', 'idle'):
                            result = view.step(event)
                            lines = strip_ansi(result.text).splitlines()
                            self.assertLessEqual(len(lines), size[1])
                            self.assertTrue(all(_text_cells(line) <= size[0] for line in lines))
                            self.assertGreaterEqual(view.state.selected_index, 0)
                            if not source:
                                self.assertEqual(result.characters, ())

    def test_pages_scroll_inside_record_before_circular_boundary_transitions(self):
        view = _View(_source({'content': list(range(25))}, {'content': list(range(25))}), size=(48, 10))
        for expected_index, expected_notice in ((1, 'Last record.'), (0, 'First record.')):
            for _ in range(40):
                before = view.render()
                offset, index = view.state.record_line_offset, view.state.selected_index
                internal = offset + max(1, before.body_rows - 1) < before.selected_line_count
                after = view.step('page_down')
                self.assertEqual(after.cursor, after.characters[0].position)
                self.assertIsNone(view.state.preferred_column)
                if internal:
                    self.assertEqual(view.state.selected_index, index)
                    self.assertGreater(view.state.record_line_offset, offset)
                    self.assertIsNone(view.state.message)
                else:
                    self.assertEqual(view.state.selected_index, expected_index)
                    self.assertEqual(view.state.record_line_offset, 0)
                    self.assertEqual(view.state.message, expected_notice)
                    break
            else:
                self.fail('page-down did not reach record boundary')
        result = view.step('page_up')
        self.assertEqual(view.state.selected_index, 1)
        self.assertGreater(view.state.record_line_offset, 0)
        self.assertEqual(result.cursor.line_index, result.characters[-1].position.line_index)
        self.assertEqual(view.state.message, 'Last record.')
        while view.state.record_line_offset:
            offset = view.state.record_line_offset
            result = view.step('page_up')
            self.assertEqual(view.state.selected_index, 1)
            self.assertLess(view.state.record_line_offset, offset)
            self.assertEqual(result.cursor.line_index, result.characters[-1].position.line_index)
            self.assertIsNone(view.state.message)
        result = view.step('page_up')
        self.assertEqual(view.state.selected_index, 0)
        self.assertGreater(view.state.record_line_offset, 0)
        self.assertEqual(result.cursor.line_index, result.characters[-1].position.line_index)
        self.assertEqual(view.state.message, 'First record.')

    def test_page_aliases_cycle_short_records_and_singleton_boundary_resets(self):
        for previous, following in (('page_up', 'page_down'), ('key\tpage_up', 'key\tpage_down'),
                                    ('line\tpgup', 'line\tpgdn'), ('text\tb', 'text\t ')):
            with self.subTest(previous=previous):
                view = _View(_source(0, 1, 2))
                view.step(previous)
                self.assertEqual(view.state.selected_index, 2)
                self.assertEqual(view.state.message, 'Last record.')
                view.step(following)
                self.assertEqual(view.state.selected_index, 0)
                self.assertEqual(view.state.message, 'First record.')
                view.step(following)
                self.assertEqual(view.state.selected_index, 1)
                self.assertIsNone(view.state.message)
        view = _View(_source({'content': list(range(20))}), size=(40, 10))
        for _ in range(40):
            view.step('page_down')
            if view.state.message == 'Only record.':
                break
        else:
            self.fail('singleton last page did not wrap')
        self.assertEqual(view.state.record_line_offset, 0)
        self.assertEqual(view.state.cursor, view.render().characters[0].position)
        result = view.step('page_up')
        self.assertEqual(view.state.message, 'Only record.')
        self.assertGreater(view.state.record_line_offset, 0)
        self.assertEqual(result.cursor.line_index, result.characters[-1].position.line_index)

    def test_page_boundary_preserves_folds_search_and_error_recovery(self):
        source = _source({'content': ['needle']}, {'content': ['needle']})
        view = _View(source)
        view.step('search\tneedle')
        view.toggle(('content',))
        search, folds = view.state.search, view.state.folds
        view.step('goto\t0')
        view.step('page_up')
        self.assertEqual(view.state.message, 'Last record.')
        self.assertFalse(view.state.message_is_error)
        view.step('page_down')
        self.assertEqual(view.state.message, 'First record.')
        self.assertEqual(view.state.search, search)
        self.assertEqual(view.state.folds, folds)
        self.assertIn('"content": [...]', view.render().text)
        view.step('key\tescape')
        self.assertIsNone(view.state.message)
        self.assertEqual(view.state.search, search)
