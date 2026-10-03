"""Blink timing and structural sibling-property navigation regressions."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer import engine
from jsonl_viewer._render import _text_cells, strip_ansi
from tests.support import FakeHost, styled_text
from tests.test_cursor_and_folding import _View, _source


class _ObservedHost(FakeHost):
    """Observe exactly the frame a user sees before each scripted input."""

    def __init__(self, events, **kwargs):
        super().__init__(events, **kwargs)
        self.observed = []

    def read_event(self):
        self.observed.append(self.frames[-1])
        return super().read_event()


class BlinkTests(unittest.TestCase):
    def test_idle_reuses_projection_and_input_restores_visible_cursor(self):
        for color in (False, True):
            with self.subTest(color=color):
                host = _ObservedHost(('idle', 'idle', 'idle', 'cursor_left', 'close'),
                                     color=color)
                with mock.patch.object(engine, 'parse_jsonl', wraps=engine.parse_jsonl) as parse, \
                     mock.patch.object(engine, 'render_frame', wraps=engine.render_frame) as render:
                    view_jsonl(_source({'content': [1, 2]}), ViewerSpec('s', 'c', 'a'), host)
                on, off, again, hidden, reset = host.observed
                self.assertEqual(on, again)
                self.assertEqual(off, hidden)
                self.assertEqual(on, reset)
                self.assertNotEqual(on, off)
                self.assertEqual(parse.call_count, 1)
                self.assertLessEqual(render.call_count, 2)
                self.assertEqual(host.close_calls, 1)
                if color:
                    self.assertEqual(strip_ansi(on), strip_ansi(off))
                    self.assertEqual(styled_text(on, '1'), styled_text(off, '1'))
                    self.assertEqual(styled_text(on, '7'), styled_text(off, '7'))
                    self.assertEqual(styled_text(on, '4'), '{')
                    self.assertEqual(styled_text(off, '4'), '')
                else:
                    before, after = on.splitlines(), off.splitlines()
                    self.assertEqual(len(before), len(after))
                    caret_row = next(i for i, line in enumerate(before) if line.strip() == '^')
                    self.assertEqual(after[caret_row].strip(), '')
                    self.assertEqual(before[:caret_row], after[:caret_row])
                    self.assertEqual(before[caret_row + 1:], after[caret_row + 1:])

    def test_character_inverse_blinks_while_matching_delimiters_stay_steady(self):
        host = _ObservedHost(('next_sibling', 'idle', 'close'), color=True)
        view_jsonl(_source({'content': 1}), ViewerSpec('s', 'c', 'a'), host)
        on, off = host.observed[1:]
        self.assertEqual(strip_ansi(on), strip_ansi(off))
        self.assertEqual(styled_text(on, '1'), styled_text(off, '1'))
        self.assertIn('c', styled_text(on, '7'))
        self.assertNotIn('c', styled_text(off, '7'))
        self.assertEqual(styled_text(on, '4'), '')
        self.assertEqual(styled_text(off, '4'), '')

    def test_prompt_and_help_pause_blink_and_preserve_literal_uppercase(self):
        for opener, closer in (('text\t/', 'key\tescape'), ('help', 'help')):
            with self.subTest(opener=opener):
                events = (opener, 'idle', 'idle', closer, 'idle', 'close')
                host = _ObservedHost(events)
                view_jsonl(_source({'content': 'JK'}), ViewerSpec('s', 'c', 'a', input_protocol='keys'), host)
                self.assertEqual(host.observed[1], host.observed[2])
                self.assertEqual(host.observed[2], host.observed[3])
                self.assertIn('^', host.observed[4])
                self.assertNotEqual(host.observed[4], host.observed[5])
        view = _View(_source({'content': 'JK'}))
        view.step('text\t/')
        view.step('text\tJK')
        self.assertEqual(view.state.prompt.buffer, 'JK')
        result = view.step('key\tenter')
        self.assertIn('1/1 occurrences', result.text)

    def test_resize_during_idle_keeps_caret_geometry_bounded(self):
        class ResizeHost(_ObservedHost):
            def read_event(self):
                event = super().read_event()
                if len(self.observed) == 1:
                    self._size = (12, 4)
                return event
        for color in (False, True):
            with self.subTest(color=color):
                host = ResizeHost(('idle', 'idle', 'close'), color=color)
                view_jsonl(_source({'content': '界e\u0301' * 30}), ViewerSpec('s', 'c', 'a'), host)
                for frame in host.observed[1:]:
                    lines = strip_ansi(frame).splitlines()
                    self.assertLessEqual(len(lines), 4)
                    self.assertTrue(all(_text_cells(line) <= 12 for line in lines))

    def test_footer_preserves_help_and_close_at_compact_width_boundaries(self):
        for width in (48, 64, 88, 90, 91, 92, 120):
            with self.subTest(width=width):
                view = _View(_source({'content': 1}), size=(width, 12))
                footer = view.render().text.splitlines()[-1]
                self.assertIn('? help • q close', footer)
                self.assertLessEqual(_text_cells(footer), width)

    def test_legacy_host_with_no_idle_keeps_static_visible_cursor(self):
        host = _ObservedHost(('cursor_left', 'close'))
        view_jsonl(_source({'content': 1}), ViewerSpec('s', 'c', 'a'), host)
        self.assertEqual(host.observed[0], host.observed[1])
        self.assertIn('^', host.observed[0])


class SiblingTests(unittest.TestCase):
    def assert_key(self, view, path, text=None, record=0):
        result = view.render()
        cell = next(cell for cell in result.characters if cell.position == result.cursor)
        self.assertTrue(cell.key_anchor, (path, cell))
        self.assertEqual(cell.property.record_index, record)
        self.assertEqual(cell.property.path, path)
        if text is not None:
            self.assertEqual(cell.text, text)
        return result

    def test_root_entry_cycles_follow_displayed_order_without_crossing_records(self):
        view = _View(_source({'content': 1, 'request_type': 2}, {'content': 3}))
        view.step('toggle_mode')
        for event, path, notice in (
            ('next_sibling', 'request_type', 'First sibling.'),
            ('previous_sibling', 'content', 'Last sibling.'),
            ('next_sibling', 'request_type', 'First sibling.'),
            ('next_sibling', 'content', 'Last sibling.'),
            ('next_sibling', 'request_type', 'First sibling.'),
        ):
            view.step(event)
            self.assert_key(view, (path,), path[0], record=0)
            self.assertEqual(view.state.message, notice)
            self.assertFalse(view.state.message_is_error)
        view.focus(view.opener())
        view.step('previous_sibling')
        self.assert_key(view, ('content',), 'c')
        self.assertEqual(view.state.message, 'Last sibling.')

    def test_nested_values_arrays_and_container_delimiters_use_owning_property(self):
        value = {'content': {'before': 0, 'group': {'one': 'needle', 'two': [7, 8]},
                             'after': [9, {'deep': 1, 'last': 2}], 'end': 3}}
        view = _View(_source(value))
        view.step('search\tneedle')
        view.step('next_sibling')
        self.assert_key(view, ('content', 'group', 'two'), 't')
        view.focus(view.opener(('content', 'group')))
        view.step('next_sibling')
        self.assert_key(view, ('content', 'after'), 'a')
        view.focus(view.opener(('content', 'after')))
        view.step('previous_sibling')
        self.assert_key(view, ('content', 'group'), 'g')
        nine = next(cell for cell in view.render().characters if cell.text == '9')
        view.focus(nine)
        view.step('next_sibling')
        self.assert_key(view, ('content', 'end'), 'e')
        view.step('search\tdeep')
        view.step('next_sibling')
        self.assert_key(view, ('content', 'after', 1, 'last'), 'l')
        closer = next(cell for cell in view.render().characters
                      if cell.delimiter == 'close' and cell.container.path == ('content', 'group'))
        view.focus(closer)
        view.step('previous_sibling')
        self.assert_key(view, ('content', 'before'), 'b')

    def test_encoded_keys_fold_search_and_input_bytes_are_preserved(self):
        source = _source({'content': json.dumps({'first': [1], 'second': ['needle', 'needle'],
                                                'third': 3})})
        view = _View(source)
        view.toggle(('content', 'second'))
        view.step('search\tfirst')
        search = view.state.search
        folds = view.state.folds
        view.step('next_sibling')
        result = self.assert_key(view, ('content', 'second'), 's')
        self.assertIn('"second": [...]', result.text)
        self.assertEqual(view.state.search, search)
        self.assertEqual(view.state.folds, folds)
        view.step('next_sibling')
        self.assert_key(view, ('content', 'third'), 't')
        self.assertEqual(view.snapshot.records[0].value, json.loads(source))

    def test_empty_wide_combining_keys_and_offscreen_jump(self):
        value = {'content': {'first': list(range(35)), '': 2, '界e\u0301': 3, '\u0301lead': 4}}
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(_source(value), size=(24, 9), color=color)
                view.step('search\tfirst')
                view.step('next_sibling')
                self.assert_key(view, ('content', ''), '"')
                self.assertGreater(view.state.record_line_offset, 0)
                view.step('next_sibling')
                self.assert_key(view, ('content', '界e\u0301'), '界')
                view.step('next_sibling')
                result = self.assert_key(view, ('content', '\u0301lead'), '\\')
                lines = strip_ansi(result.text).splitlines()
                self.assertTrue(all(_text_cells(line) <= 24 for line in lines))
                view.step('previous_sibling')
                self.assert_key(view, ('content', '界e\u0301'), '界')

    def test_simple_mode_excludes_hidden_properties_and_scalars_have_no_siblings(self):
        view = _View(_source({'hidden': 1, 'content': 2, 'also_hidden': 3}))
        view.step('next_sibling')
        self.assert_key(view, ('content',), 'c')
        before = view.state.cursor
        view.step('next_sibling')
        self.assertEqual(view.state.cursor, before)
        self.assertNotIn('"hidden"', view.render().text)
        for value in (1, 'scalar', [], {}, [1, 2]):
            with self.subTest(value=value):
                view = _View(_source(value))
                before = view.render().cursor
                view.step('next_sibling')
                view.step('previous_sibling')
                self.assertEqual(view.state.cursor, before)

    def test_physical_line_and_batched_input_refresh_live_property_metadata(self):
        source = _source({'content': {'first': 1, 'second': 2, 'third': 3}})
        for event in ('next_sibling', 'text\tJ', 'line\tJ'):
            with self.subTest(event=event):
                view = _View(source)
                view.step('search\tfirst')
                view.step(event)
                self.assert_key(view, ('content', 'second'), 's')
        for batch in ('JJ', 'JK', 'JKhJ'):
            with self.subTest(batch=batch):
                combined, separate = _View(source), _View(source)
                combined.step('search\tfirst')
                separate.step('search\tfirst')
                actual = combined.step('text\t' + batch)
                for character in batch:
                    expected = separate.step('text\t' + character)
                self.assertEqual(actual.text, expected.text)
                self.assertEqual(combined.state, separate.state)

    def test_action_after_hidden_phase_preserves_search_and_folds(self):
        source = _source({'content': {'first': [1], 'second': [2]}})
        for idle in ((), ('idle',)):
            host = _ObservedHost(('search\tfirst', 'toggle_fold', *idle,
                                  'next_sibling', 'close'))
            view_jsonl(source, ViewerSpec('s', 'c', 'a'), host)
            if not idle:
                expected = host.observed[-1]
            else:
                self.assertEqual(host.observed[-1], expected)
                self.assertIn('1/1 occurrences', host.observed[-1])

    def test_folded_root_cannot_expose_hidden_keys(self):
        view = _View(_source({'content': {'one': 1, 'two': 2}}))
        view.toggle(())
        before = view.render()
        folds = view.state.folds
        for event in ('next_sibling', 'previous_sibling'):
            after = view.step(event)
            self.assertEqual(after.cursor, before.cursor)
            self.assertEqual(after.text, before.text)
            self.assertEqual(view.state.folds, folds)

    def test_deep_clipped_key_is_revealed_without_unfolding_or_changing_mode(self):
        payload = {'first': 1, 'second': 2}
        for index in range(10):
            payload = {f'layer{index}': payload}
        view = _View(_source({'content': payload}), size=(16, 8))
        view.step('search\tfirst')
        mode, folds = view.state.mode, view.state.folds
        view.step('next_sibling')
        path = ('content', *(f'layer{index}' for index in reversed(range(10))), 'second')
        result = self.assert_key(view, path, 's')
        self.assertEqual(view.state.mode, mode)
        self.assertEqual(view.state.folds, folds)
        self.assertTrue(all(_text_cells(line) <= 16 for line in strip_ansi(result.text).splitlines()))

    def test_fold_after_sibling_navigation_lands_on_container_not_key(self):
        view = _View(_source({'content': {'first': 1, 'second': [2, 3]}}))
        view.step('search\tfirst')
        view.step('next_sibling')
        self.assert_key(view, ('content', 'second'), 's')
        for _ in range(20):
            result = view.render()
            focused = next(cell for cell in result.characters if cell.position == result.cursor)
            if focused.delimiter == 'open':
                break
            view.step('cursor_right')
        else:
            self.fail('container opener not reached from sibling key')
        folded = view.step('toggle_fold')
        focused = next(cell for cell in folded.characters if cell.position == folded.cursor)
        self.assertEqual(focused.delimiter, 'open')
        self.assertEqual(focused.container.path, ('content', 'second'))
        self.assertIn('"second": [...]', strip_ansi(folded.text))

    def test_arrival_notices_clear_on_interior_and_ordinary_cursor_movement(self):
        source = _source({'content': {'alpha': 1, 'beta': 2, 'gamma': 3}})
        for color in (False, True):
            with self.subTest(color=color):
                view = _View(source, color=color, size=(48, 12))
                view.step('search\tbeta')
                for event, key, notice in (
                    ('previous_sibling', 'alpha', 'First sibling.'),
                    ('previous_sibling', 'gamma', 'Last sibling.'),
                    ('next_sibling', 'alpha', 'First sibling.'),
                    ('next_sibling', 'beta', None),
                    ('next_sibling', 'gamma', 'Last sibling.'),
                    ('previous_sibling', 'beta', None),
                ):
                    result = view.step(event)
                    self.assert_key(view, ('content', key), key[0])
                    self.assertEqual(view.state.message, notice)
                    if notice:
                        self.assertIn(notice, strip_ansi(result.text))
                view.step('next_sibling')
                view.step('cursor_right')
                self.assertIsNone(view.state.message)

    def test_singleton_focuses_key_and_root_empty_folded_or_unowned_stays_inert(self):
        view = _View(_source({'content': {'only': 'value'}}))
        view.step('search\tvalue')
        for event in ('next_sibling', 'previous_sibling'):
            result = view.step(event)
            self.assert_key(view, ('content', 'only'), 'o')
            self.assertEqual(view.state.message, 'Only sibling.')
            self.assertIn('Only sibling.', result.text)
        for value in ({}, [], 1, [1, 2]):
            view = _View(_source(value))
            before = view.render()
            self.assertEqual(view.step('next_sibling').text, before.text)
            self.assertEqual(view.step('previous_sibling').text, before.text)
        view = _View(_source({'content': {'only': 1}}))
        before = view.toggle(())
        self.assertEqual(view.step('next_sibling').text, before.text)
        self.assertIsNone(view.state.message)

    def test_notice_survives_idle_and_resize_and_escape_preserves_active_search(self):
        source = _source({'content': {'first': 'needle', 'last': 'needle'}})
        view = _View(source)
        view.step('search\tneedle')
        search = view.state.search
        view.step('previous_sibling')
        self.assertEqual(view.state.message, 'Last sibling.')
        position, folds = view.state.cursor, view.state.folds
        view.step('idle')
        self.assertEqual((view.state.cursor, view.state.folds, view.state.search),
                         (position, folds, search))
        self.assertEqual(view.state.message, 'Last sibling.')
        view.size = (24, 9)
        self.assertIn('Last sibling.', view.render().text)
        view.step('key\tescape')
        self.assertIsNone(view.state.message)
        self.assertEqual(view.state.search, search)
        view.step('key\tescape')
        self.assertIsNone(view.state.search)
        self.assertEqual(view.state.message, 'Search cleared.')
        host = _ObservedHost(('search\tneedle', 'previous_sibling', 'idle', 'text\t/', 'close'))
        view_jsonl(source, ViewerSpec('s', 'c', 'a', input_protocol='keys'), host)
        self.assertIn('Last sibling.', host.observed[2])
        self.assertIn('Last sibling.', host.observed[3])
        self.assertIn('Search query:', host.observed[4])
        self.assertNotIn('Last sibling.', host.observed[4])

    def test_encoded_folded_offscreen_siblings_cycle_without_changing_search_or_folds(self):
        payload = {'first': list(range(30)), 'middle': 2, 'last': [3, 4]}
        source = _source({'content': json.dumps(payload)}, {'content': payload})
        view = _View(source, size=(32, 10))
        view.step('search\tlast')
        view.toggle(('content', 'last'))
        view.step('search\tlast')
        search, folds = view.state.search, view.state.folds
        for event, key in (('next_sibling', 'first'), ('previous_sibling', 'last'),
                           ('previous_sibling', 'middle'), ('previous_sibling', 'first')):
            view.step(event)
            self.assert_key(view, ('content', key), key[0], record=0)
            self.assertEqual(view.state.search, search)
            self.assertEqual(view.state.folds, folds)
        self.assertEqual(view.snapshot.records[0].value, json.loads(source.splitlines()[0]))

    def test_all_key_protocols_wrap_and_batched_cycles_keep_final_notice(self):
        source = _source({'content': {'first': 1, 'last': 2}})
        for forward, backward in (('next_sibling', 'previous_sibling'),
                                  ('text\tJ', 'text\tK'), ('line\tJ', 'line\tK')):
            view = _View(source)
            view.step('search\tlast')
            view.step(forward)
            self.assert_key(view, ('content', 'first'), 'f')
            self.assertEqual(view.state.message, 'First sibling.')
            view.step(backward)
            self.assert_key(view, ('content', 'last'), 'l')
            self.assertEqual(view.state.message, 'Last sibling.')
        combined, separate = _View(source), _View(source)
        for view in (combined, separate):
            view.step('search\tfirst')
        actual = combined.step('text\tJJJKK')
        for key in 'JJJKK':
            expected = separate.step('text\t' + key)
        self.assertEqual(actual.text, expected.text)
        self.assertEqual(combined.state, separate.state)
