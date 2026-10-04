"""Character traversal across hidden rows uses focused-record geometry."""

from __future__ import annotations

from dataclasses import replace
import json
import unittest

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer._render import _text_cells, strip_ansi
from tests.test_blink_and_siblings import _ObservedHost
from tests.test_cursor_and_folding import _View, _source


def _reference(view):
    reference = _View(b'', size=(view.size[0], 200), color=view.color)
    reference.snapshot = view.snapshot
    reference.state = replace(view.state, selected_index=0, record_line_offset=0,
                              cursor=None, preferred_column=None, reveal_match=False,
                              focus_container=None, focus_property=None)
    return reference.render().characters


class CursorPageCrossingTests(unittest.TestCase):
    def test_horizontal_full_cycles_visit_each_projected_cell_once(self):
        source = _source({'content': ['界e\u0301', 'abcdef', 1, 2, 3, 4]},
                         {'content': ['終😀', 5, 6, 7, 8, 9]})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, size=(32, 9), color=color)
                expected = [cell.position for cell in _reference(view)]
                self.assertGreater(len(expected), len(view.render().characters))
                self.assertEqual(view.render().cursor, expected[0])
                for position in expected[1:] + expected[:1]:
                    actual = view.step('cursor_right')
                    self.assertEqual(actual.cursor, position)
                    self.assertEqual(view.state.preferred_column, position.column)
                for position in list(reversed(expected[1:])) + expected[:1]:
                    actual = view.step('cursor_left')
                    self.assertEqual(actual.cursor, position)
                    self.assertEqual(view.state.preferred_column, position.column)

    def test_vertical_cycles_preserve_preferred_column_through_short_rows(self):
        source = _source({'content': {'long': 'abcdefghijklmnop', 'x': 1,
                                     'items': [1, 2, 3, 4, 5, 6]}},
                         {'content': {'tail': '界e\u0301 abcdefghijklmnop', 'z': 0}})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, size=(48, 10), color=color)
                reference = _reference(view)
                groups = {}
                for cell in reference:
                    groups.setdefault((cell.position.record_index, cell.position.line_index), []).append(cell)
                rows = list(groups)
                start = next(cell for cell in view.render().characters if cell.position.column >= 24)
                view.focus(start)
                first = rows.index((start.position.record_index, start.position.line_index))
                preferred = start.position.column
                for event, direction in (('cursor_down', 1), ('cursor_up', -1)):
                    for distance in range(1, len(rows) + 1):
                        target = groups[rows[(first + direction * distance) % len(rows)]]
                        expected = min(target, key=lambda cell: (abs(cell.position.column - preferred), cell.position.column))
                        actual = view.step(event)
                        self.assertEqual(actual.cursor, expected.position)
                        self.assertEqual(view.state.preferred_column, preferred)

    def test_partial_following_record_uses_its_own_line_index_without_notice(self):
        source = _source(0, {'content': list(range(25))}, {'content': 99})
        for event in ('cursor_right', 'cursor_down'):
            view = _View(source, size=(48, 12))
            before = view.render()
            edge = before.characters[-1]
            self.assertEqual(edge.position.record_index, 1)
            self.assertEqual(view.state.selected_index, 0)
            view.focus(edge)
            reference = _reference(view)
            if event == 'cursor_right':
                expected = reference[[c.position for c in reference].index(edge.position) + 1]
            else:
                next_row = [cell for cell in reference if cell.position.record_index == 1
                            and cell.position.line_index == edge.position.line_index + 1]
                expected = min(next_row, key=lambda cell: (abs(cell.position.column - edge.position.column), cell.position.column))
            after = view.step(event)
            self.assertEqual(after.cursor, expected.position)
            self.assertEqual(view.state.selected_index, 1)
            self.assertIsNone(view.state.message)
            self.assertEqual(view.step('cursor_left' if event == 'cursor_right' else 'cursor_up').cursor,
                             edge.position)

    def test_backward_snapshot_boundary_reveals_previous_records_final_row(self):
        source = _source({'content': list(range(20))}, {'content': list(range(30))})
        for event in ('cursor_left', 'cursor_up'):
            view = _View(source, size=(40, 9))
            reference = _reference(view)
            expected = reference[-1].position
            result = view.step(event)
            self.assertEqual(result.cursor, expected)
            self.assertEqual(view.state.selected_index, 1)
            self.assertGreater(view.state.record_line_offset, 0)
            self.assertEqual(view.state.message, 'Last record.')
            self.assertEqual(view.step('cursor_right' if event == 'cursor_left' else 'cursor_down').cursor,
                             reference[0].position)
            self.assertEqual(view.state.message, 'First record.')

    def test_folded_encoded_projection_and_search_state_survive_auto_paging(self):
        source = _source({'content': json.dumps({'first': list(range(20)), 'last': ['needle', 'needle']})})
        view = _View(source, size=(32, 10))
        view.toggle(('content', 'first'))
        view.step('search\tneedle')
        search, folds, mode = view.state.search, view.state.folds, view.state.mode
        for event in ('cursor_down', 'cursor_up', 'cursor_right', 'cursor_left'):
            edge = view.render().characters[-1 if event in ('cursor_down', 'cursor_right') else 0]
            view.focus(edge)
            view.step(event)
            self.assertEqual((view.state.search, view.state.folds, view.state.mode), (search, folds, mode))
            self.assertFalse(view.state.reveal_match)
        self.assertEqual(view.snapshot.records[0].value, json.loads(source))

    def test_annotation_only_rows_and_tiny_frames_remain_bounded(self):
        nested = {'leaf': '界e\u0301' * 10}
        for _ in range(8):
            nested = {'deep': nested}
        source = _source({'content': json.dumps(nested, ensure_ascii=False)})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, size=(12, 4), color=color)
                expected = [cell.position for cell in _reference(view)]
                for position in expected[1:] + expected[:1]:
                    result = view.step('cursor_right')
                    self.assertEqual(result.cursor, position)
                    lines = strip_ansi(result.text).splitlines()
                    self.assertLessEqual(len(lines), 4)
                    self.assertTrue(all(_text_cells(line) <= 12 for line in lines))
                    self.assertTrue(result.characters)

                rows = {}
                for cell in _reference(view):
                    rows.setdefault(cell.position.line_index, cell.position)
                ordered = list(rows.values())
                for event, positions in (
                    ('page_down', ordered[1:] + ordered[:1]),
                    ('page_up', list(reversed(ordered[1:])) + ordered[:1]),
                ):
                    for position in positions:
                        result = view.step(event)
                        self.assertEqual(result.cursor, position)
                        self.assertIsNone(view.state.preferred_column)

    def test_visible_record_neighbors_do_not_move_viewport_or_emit_notice(self):
        view = _View(_source(1, 2, 3))
        for event in ('cursor_right', 'cursor_down'):
            view.focus(view.render().characters[0])
            result = view.step(event)
            self.assertEqual(result.cursor.record_index, 1)
            self.assertEqual(view.state.selected_index, 0)
            self.assertEqual(view.state.record_line_offset, 0)
            self.assertIsNone(view.state.message)

    def test_idle_and_prompt_input_do_not_repeat_auto_paging(self):
        source = _source({'content': list(range(20))})
        host = _ObservedHost(('cursor_left', 'idle', 'text\t/', 'text\thjkl', 'key\tescape', 'close'), size=(40, 9))
        view_jsonl(source, ViewerSpec('s', 'c', 'a', input_protocol='keys'), host)
        self.assertIn('Only record.', host.observed[1])
        self.assertIn('Only record.', host.observed[2])
        self.assertIn('Search query: hjkl', host.observed[4])
        before = [line for line in host.observed[1].splitlines() if '│' in line]
        after = [line for line in host.observed[5].splitlines() if '│' in line]
        self.assertEqual(before, after)

    def test_batched_physical_keys_refresh_geometry_after_each_page_crossing(self):
        source = _source({'content': list(range(15))}, {'content': list(range(12))})
        batch, individual = _View(source, size=(32, 9)), _View(source, size=(32, 9))
        keys = 'l' * 90 + 'h' * 45 + 'j' * 20 + 'k' * 12
        actual = batch.step('text\t' + keys)
        for key in keys:
            expected = individual.step('text\t' + key)
        self.assertEqual(actual.text, expected.text)
        self.assertEqual(batch.state, individual.state)

    def test_public_loop_preserves_revealed_deep_key_across_cursor_pages(self):
        nested = {'firstkey': 1, 'target': 2}
        for _ in range(8):
            nested = {'nest': nested}
        source = _source(nested)
        view = _View(source, size=(12, 4), color=True)
        events = ('search\tfirstkey', 'next_sibling', 'cursor_down', 'cursor_up')
        expected = [view.render().text]
        expected.extend(view.step(event).text for event in events)
        host = _ObservedHost((*events, 'close'), size=view.size, color=True)
        view_jsonl(source, view.spec, host)
        self.assertEqual(host.observed, expected)
        before = host.observed[2].splitlines()[1]
        after = host.observed[4].splitlines()[1]
        self.assertEqual(before, after)
        self.assertIn('⟦t⟧', strip_ansi(before))
        self.assertEqual(host.close_calls, 1)

    def test_expansion_limit_root_keeps_a_navigable_cell_between_records(self):
        encoded = '{}'
        for _ in range(17):
            encoded = json.dumps(encoded)
        source = _source(0, encoded, 2)
        for color in (False, True):
            view = _View(source, size=(12, 4), color=color)
            for event, expected in (('cursor_down', 1), ('cursor_down', 2),
                                    ('cursor_down', 0), ('cursor_up', 2),
                                    ('cursor_up', 1), ('cursor_up', 0)):
                result = view.step(event)
                self.assertTrue(result.characters)
                self.assertEqual(result.cursor.record_index, expected)
                self.assertTrue(all(_text_cells(line) <= 12 for line in strip_ansi(result.text).splitlines()))
            for _ in range(50):
                result = view.step('cursor_right')
                if result.cursor.record_index == 2:
                    break
            else:
                self.fail('horizontal cursor stalled at expansion-limit record')
            self.assertEqual(view.snapshot.records[1].value, encoded)
