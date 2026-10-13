"""Owned terminal capture, cleanup, and embedding-buffer regression tests."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import inspect
import io
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonl_viewer import ViewerSpec, ViewerTerminal, view_jsonl
from jsonl_viewer import _terminal
from tests.support import FakeHost
from tests import test_standalone_and_packaging as standalone_tests
from tests.test_standalone_and_packaging import _TerminalBuffer
from tests.terminal_screen import TerminalScreen


_ENTER = "\x1b[?1049h\x1b[?25l\x1b[?1006h\x1b[?1000h"
_LEAVE = "\x1b[?1000l\x1b[?1006l\x1b[H\x1b[2J\x1b[?25h\x1b[?1049l"
_SOURCE = b'{"content":"one"}\n{"content":"two"}\n'
_SPEC = ViewerSpec("s", "c", "a")


@contextmanager
def _acquired(terminal, **kwargs):
    saved = [1, 2, 3, 72, 4, 5, [b"\x03", b"\x04"]]
    termios = mock.Mock(TCSANOW=0, TCSADRAIN=1, ECHO=8, ECHONL=64, VINTR=0, VEOF=1)
    termios.tcgetattr.side_effect = lambda descriptor: copy.deepcopy(saved)
    tty = mock.Mock()
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(_terminal, "termios", termios))
        stack.enter_context(mock.patch.object(_terminal, "tty", tty))
        stack.enter_context(mock.patch.object(_terminal.signal, "raise_signal"))
        dispositions = {signal.SIGTERM: signal.SIG_DFL, signal.SIGHUP: signal.SIG_DFL}
        def install(number, handler):
            previous = dispositions[number]
            dispositions[number] = handler
            return previous
        stack.enter_context(mock.patch.object(_terminal.signal, "getsignal", side_effect=dispositions.__getitem__))
        handlers = stack.enter_context(mock.patch.object(_terminal.signal, "signal", side_effect=install))
        stack.enter_context(mock.patch.dict(_terminal.os.environ, {"TERM": "xterm-256color"}))
        host = _terminal._TerminalHost(terminal, **kwargs)
        yield host, termios, tty, handlers, saved


class OwnedTerminalTests(unittest.TestCase):
    def test_public_signature_defaults_and_immutable_terminal_adapter(self):
        signature = inspect.signature(view_jsonl)
        self.assertEqual(tuple(signature.parameters),
                         ("source", "spec", "host", "terminal", "transient_only"))
        self.assertIsNone(signature.parameters["host"].default)
        self.assertIsNone(signature.parameters["terminal"].default)
        self.assertIs(signature.parameters["transient_only"].default, False)
        self.assertEqual(signature.parameters["terminal"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(tuple(inspect.signature(ViewerTerminal).parameters),
                         ("input_stream", "output_stream", "read_character", "no_color"))
        streams = (io.StringIO(), io.StringIO())
        terminal = ViewerTerminal(*streams)
        self.assertIsNone(terminal.read_character)
        self.assertFalse(terminal.no_color)
        with self.assertRaises(AttributeError):
            terminal.no_color = True

    def test_invalid_or_conflicting_ownership_is_rejected_before_io(self):
        input_stream, output_stream = mock.Mock(), mock.Mock()
        terminal = ViewerTerminal(input_stream, output_stream)
        host = FakeHost()
        for kwargs in ({"host": host, "terminal": terminal},
                       {"host": host, "transient_only": True},
                       {"terminal": object()}, {"transient_only": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                view_jsonl(_SOURCE, _SPEC, **kwargs)
        input_stream.assert_not_called()
        output_stream.assert_not_called()
        self.assertEqual(input_stream.mock_calls, [])
        self.assertEqual(output_stream.mock_calls, [])
        self.assertEqual(host.frames, [])
        self.assertEqual(host.close_calls, 0)

    def test_borrowed_host_preserves_existing_semantic_contract(self):
        host = FakeHost(("goto\t2", "close"))
        with mock.patch.object(_terminal, "view_owned") as owned:
            view_jsonl(_SOURCE, _SPEC, host)
        owned.assert_not_called()
        self.assertIn('"content": "two"', host.frames[-1])
        self.assertNotIn('"content": "one"', host.frames[-1])
        self.assertEqual(_SPEC.input_protocol, "semantic")
        self.assertEqual(host.close_calls, 1)

    def test_owned_line_fallback_uses_keys_without_mutating_spec_or_closing_streams(self):
        input_stream, output_stream = io.StringIO("g 2\nq\nTAIL\n"), io.StringIO()
        view_jsonl(_SOURCE, _SPEC, terminal=ViewerTerminal(input_stream, output_stream, no_color=True))
        self.assertIn("Moved to source line 2.", output_stream.getvalue())
        self.assertNotIn("\x1b", output_stream.getvalue())
        self.assertEqual(_SPEC.input_protocol, "semantic")
        self.assertEqual(input_stream.readline(), "TAIL\n")
        self.assertFalse(input_stream.closed)
        self.assertFalse(output_stream.closed)

    def test_transient_only_refuses_unsupported_output_before_snapshot_content(self):
        for terminal in (ViewerTerminal(io.StringIO("q\n"), io.StringIO()),
                         ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())):
            with self.subTest(terminal=terminal), mock.patch.dict(_terminal.os.environ, {"TERM": "dumb"}):
                with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                    view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                self.assertEqual(terminal.output_stream.getvalue(), "")
                self.assertFalse(terminal.input_stream.closed)
                self.assertFalse(terminal.output_stream.closed)

    def test_geometry_uses_supplied_output_and_positive_environment_overrides(self):
        class OutputBuffer(_TerminalBuffer):
            def fileno(self):
                return 29
        terminal = ViewerTerminal(_TerminalBuffer(), OutputBuffer())
        with _acquired(terminal) as (host, _, _, _, _), mock.patch.object(
                _terminal.os, "get_terminal_size", return_value=os.terminal_size((88, 22))) as size, \
                mock.patch.dict(os.environ, {"TERM": "xterm"}, clear=True), host:
            size.reset_mock()
            self.assertEqual(host.terminal_size(), (88, 22))
            size.assert_called_once_with(29)
            for overrides, expected in (({"COLUMNS": "120", "LINES": "24"}, (88, 22)),
                                        ({"COLUMNS": "120"}, (88, 22)),
                                        ({"LINES": "24"}, (88, 22)),
                                        ({"COLUMNS": "0", "LINES": "-1"}, (88, 22)),
                                        ({"COLUMNS": "bad", "LINES": ""}, (88, 22))):
                with self.subTest(overrides=overrides), mock.patch.dict(os.environ, overrides, clear=True):
                    self.assertEqual(host.terminal_size(), expected)

    def test_acquisition_disables_echo_and_cleanup_restores_exact_modes_in_order(self):
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
        with _acquired(terminal) as (host, termios, tty, handlers, saved):
            with host:
                self.assertEqual(terminal.output_stream.getvalue(), _ENTER)
                tty.setcbreak.assert_called_once_with(17, 0)
                active = termios.tcsetattr.call_args.args[2]
                self.assertEqual(active[3] & (8 | 64), 0)
                host.present("VIEW FRAME")
            self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
            self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))
            self.assertEqual(termios.tcsetattr.call_count, 2)
            installed = [call.args[0] for call in handlers.call_args_list[:2]]
            self.assertEqual(installed, [signal.SIGTERM, signal.SIGHUP])
            self.assertEqual([call.args for call in handlers.call_args_list[2:]],
                             [(signal.SIGHUP, signal.SIG_DFL), (signal.SIGTERM, signal.SIG_DFL)])
            host._restore_terminal()
            self.assertEqual(termios.tcsetattr.call_count, 2)
            self.assertEqual(terminal.output_stream.getvalue().count(_LEAVE), 1)
        self.assertFalse(terminal.input_stream.closed)
        self.assertFalse(terminal.output_stream.closed)

    def test_scoped_signals_leave_custom_ignored_and_sigint_handlers_alone(self):
        custom = lambda number, frame: None
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
        for dispositions in ({signal.SIGTERM: custom, signal.SIGHUP: signal.SIG_IGN},
                             {signal.SIGTERM: signal.SIG_IGN, signal.SIGHUP: custom}):
            with self.subTest(dispositions=dispositions), _acquired(terminal) as (host, _, _, handlers, _):
                with mock.patch.object(_terminal.signal, "getsignal", side_effect=dispositions.__getitem__):
                    with host:
                        pass
                handlers.assert_not_called()
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
        with _acquired(terminal) as (host, _, _, handlers, _):
            with mock.patch.object(_terminal.threading, "current_thread", return_value=object()):
                with self.assertRaisesRegex(RuntimeError, "main thread"):
                    host.__enter__()
                self.assertEqual(terminal.output_stream.getvalue(), "")
            handlers.assert_not_called()

    def test_default_signal_cleanup_restores_before_redelivery_and_ignores_repetition(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
            with self.subTest(number=number), _acquired(terminal) as (host, termios, _, handlers, saved):
                def redeliver(delivered):
                    self.assertEqual(delivered, number)
                    self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
                    self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))
                    self.assertEqual(_terminal.signal.getsignal(number), signal.SIG_DFL)
                _terminal.signal.raise_signal.side_effect = redeliver
                with self.assertRaises(_terminal._TerminalTermination) as failure, host:
                    callback = next(call.args[1] for call in handlers.call_args_list
                                    if call.args[0] == number)
                    try:
                        callback(number, None)
                    except _terminal._TerminalTermination:
                        callback(number, None)
                        raise
                self.assertEqual(failure.exception.number, number)
                _terminal.signal.raise_signal.assert_called_once_with(number)

    def test_close_view_restores_resources_before_context_exit_and_is_idempotent(self):
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
        with _acquired(terminal) as (host, termios, _, _, saved):
            with host:
                host.present("frame")
                host.close_view()
                self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
                self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))
                host.close_view()
                self.assertEqual(terminal.output_stream.getvalue().count(_LEAVE), 1)
            self.assertEqual(terminal.output_stream.getvalue().count(_LEAVE), 1)

    def test_setup_and_view_failures_restore_after_partial_acquisition(self):
        class FailOnceOutput(_TerminalBuffer):
            fail_write = True
            def write(self, text):
                result = super().write(text)
                if self.fail_write:
                    self.fail_write = False
                    raise RuntimeError("partial mode write")
                return result
        terminal = ViewerTerminal(_TerminalBuffer(), FailOnceOutput())
        with _acquired(terminal) as (host, termios, _, _, saved):
            with self.assertRaisesRegex(RuntimeError, "partial mode write"):
                host.__enter__()
            self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
            self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
        with _acquired(terminal) as (host, termios, tty, handlers, saved):
            tty.setcbreak.side_effect = RuntimeError("partial cbreak")
            with self.assertRaisesRegex(RuntimeError, "partial cbreak"):
                host.__enter__()
            self.assertEqual(terminal.output_stream.getvalue(), "")
            self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))
            self.assertEqual(handlers.call_count, 4)
        for failure in (RuntimeError("frame failed"), KeyboardInterrupt(), EOFError()):
            terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer())
            with self.subTest(failure=type(failure).__name__), _acquired(terminal) as (host, termios, _, _, saved):
                with self.assertRaises(type(failure)), host:
                    raise failure
                self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
                self.assertEqual(termios.tcsetattr.call_args.args, (17, 1, saved))

    def test_callback_reads_buffer_first_and_receives_readiness_hook(self):
        buffered = list("界qZ")
        hooks = []
        def reader(before_read=None):
            hooks.append(before_read)
            if buffered:
                return buffered.pop(0)
            before_read()
            raise EOFError
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer(), read_character=reader)
        with _acquired(terminal) as (host, _, _, _, _), mock.patch.object(_terminal.select, "select") as poll:
            with host:
                self.assertEqual(host.read_event(), "text\t界")
                self.assertEqual(host.read_event(), "text\tq")
                self.assertEqual(buffered, ["Z"])
                poll.assert_not_called()
                self.assertTrue(all(callable(hook) for hook in hooks))
                buffered.clear()
                poll.return_value = ([], [], [])
                self.assertEqual(host.read_event(), "idle")
                poll.assert_called_once()

    def test_owned_close_never_flushes_or_reads_following_buffered_input(self):
        queued = list("q\x1b[<65;1;1Mnext")
        def reader(before_read=None):
            return queued.pop(0)
        terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer(), read_character=reader)
        with _acquired(terminal), mock.patch.object(_terminal.os, "read") as raw_read, \
                mock.patch.object(_terminal.termios, "tcflush", create=True) as flush:
            view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
            self.assertEqual("".join(queued), "\x1b[<65;1;1Mnext")
            raw_read.assert_not_called()
            flush.assert_not_called()
            self.assertNotIn("mouse", terminal.output_stream.getvalue())

    def test_mouse_decoder_preserves_trailing_keyboard_and_partial_reports(self):
        cases = (
            (list("\x1b[<65;9;8Mq"), ["mouse\t65\t9\t8\tpress", "text\tq"]),
            (list("\x1b[<0;9;8mq"), ["mouse\t0\t9\t8\trelease", "text\tq"]),
            (list("\x1b[<0;0;8Mq"), ["mouse\tinvalid", "text\tq"]),
            (list("\x1b[<65;9;q"), ["mouse\tinvalid", "text\tq"]),
            (list("\x1b[<" + "0" * 50 + ";8;8Mq"), ["mouse\tinvalid", "text\tq"]),
            (list("\x1b[") + [_terminal._ReadTimeout()] + list("<65;9;8Mq"),
             ["mouse\tinvalid", "mouse\t65\t9\t8\tpress", "text\tq"]),
            (list("\x1b[") + [_terminal._ReadTimeout()] + list("q"),
             ["mouse\tinvalid", "mouse\tinvalid", "text\tq"]),
            (list("\x1b[<") + [_terminal._ReadTimeout()] + list("65;9;8Mq"),
             ["mouse\tinvalid", "mouse\tinvalid", "text\tq"]),
            (list("\x1b[<65;") + [_terminal._ReadTimeout()] + list("9;8Mq"),
             ["mouse\tinvalid", "mouse\tinvalid", "text\tq"]),
            (list("\x1b[<65;9;8M\x1b[<64;9;8Mq"),
             ["mouse\t65\t9\t8\tpress", "mouse\t64\t9\t8\tpress", "text\tq"]),
        )
        for script, expected in cases:
            queued = list(script)
            def reader(before_read=None):
                if not queued:
                    raise EOFError
                value = queued.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value
            terminal = ViewerTerminal(_TerminalBuffer(), _TerminalBuffer(), read_character=reader)
            with self.subTest(script=script), _acquired(terminal) as (host, _, _, _, _):
                with host:
                    self.assertEqual([host.read_event() for _ in expected], expected)
                    self.assertIsNone(host.read_event())
                self.assertEqual(queued, [])
                self.assertNotIn("65;", terminal.output_stream.getvalue())
                self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))


class MousePtyTests(unittest.TestCase):
    _start_pty = standalone_tests.StandaloneTests._start_pty
    _pty_read_until = standalone_tests.StandaloneTests._pty_read_until
    _pty_frame_containing = standalone_tests.StandaloneTests._pty_frame_containing
    _pty_write = standalone_tests.StandaloneTests._pty_write
    _pty_finish = standalone_tests.StandaloneTests._pty_finish
    _pty_cleanup = standalone_tests.StandaloneTests._pty_cleanup

    def test_pty_mouse_report_is_captured_not_echoed_and_cleanup_restores_terminal(self):
        if _terminal.termios is None:
            self.skipTest("POSIX terminal control unavailable")
        termios = _terminal.termios
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.jsonl"
            path.write_bytes(_SOURCE)
            process, master = self._start_pty(path)
            output = bytearray()
            try:
                frame, end = self._pty_frame_containing(master, output, b"Record 1/2", start=0)
                self.assertTrue(bytes(output).startswith(_ENTER.encode()))
                active = termios.tcgetattr(master)
                self.assertFalse(active[3] & (termios.ECHO | termios.ECHONL))
                self._pty_write(master, b"\x1b[<65;8;8M")
                moved, end = self._pty_frame_containing(master, output, b"Record 1/2", start=end)
                self.assertNotEqual(moved, frame)
                self._pty_write(master, b"\x1b[<0;9;8mq")
                self._pty_read_until(master, output, _LEAVE.encode(), start=end)
                self._pty_finish(process, master, output)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(termios.tcgetattr(master), self._pty_original_attributes)
                self.assertNotIn(b"65;8;8M", output)
                self.assertNotIn(b"0;9;8m", output)
                self.assertEqual(path.read_bytes(), _SOURCE)
            finally:
                self._pty_cleanup(process, master)

    def test_pty_default_sigterm_restores_mouse_screen_and_attributes(self):
        if _terminal.termios is None:
            self.skipTest("POSIX terminal control unavailable")
        termios = _terminal.termios
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.jsonl"
            path.write_bytes(_SOURCE)
            process, master = self._start_pty(path)
            output = bytearray()
            try:
                _, end = self._pty_frame_containing(master, output, b"Record 1/2", start=0)
                process.send_signal(signal.SIGTERM)
                self._pty_read_until(master, output, _LEAVE.encode(), start=end)
                self._pty_finish(process, master, output)
                self.assertEqual(process.returncode, -signal.SIGTERM)
                self.assertEqual(termios.tcgetattr(master), self._pty_original_attributes)
            finally:
                self._pty_cleanup(process, master)


class _ScreenOutput(_TerminalBuffer):
    def __init__(self, screen):
        super().__init__()
        self.screen = screen

    def fileno(self):
        return 29

    def write(self, text):
        result = super().write(text)
        # Simulate the ordinary POSIX ONLCR output mapping outside the model.
        self.screen.feed(text.replace("\n", "\r\n"))
        return result


@contextmanager
def _observed(columns=72, rows=10, *, archive_ed2=False, eager_wrap=False,
              reader=None, overrides=None):
    screen = TerminalScreen(columns, rows, archive_ed2=archive_ed2,
                            eager_wrap=eager_wrap)
    screen.feed("\x1b[1;1HP\x1b[2;1HS\x1b[3;2H")
    original = screen.primary_state()
    terminal = ViewerTerminal(_TerminalBuffer(), _ScreenOutput(screen),
                              read_character=reader)
    with _acquired(terminal) as (host, _, _, _, _), \
            mock.patch.dict(os.environ, {"TERM": "xterm-256color", **(overrides or {})},
                            clear=True), \
            mock.patch.object(_terminal.os, "get_terminal_size",
                              side_effect=lambda fd: os.terminal_size(
                                  (screen.columns, screen.rows))) as size:
        yield host, screen, original, terminal, size


class ScreenRestorationTests(unittest.TestCase):
    def _assert_restored(self, screen, original):
        self.assertEqual(screen.primary_state(), original)
        self.assertEqual(screen.alternate_scrolls, 0)

    def test_screen_model_distinguishes_archiving_erase_and_delayed_wrap(self):
        for archiving in (False, True):
            with self.subTest(archiving=archiving):
                screen = TerminalScreen(4, 2, archive_ed2=archiving)
                screen.feed("P\x1b[2;2H")
                original = screen.primary_state()
                screen.feed("\x1b[?1049hABCD")
                self.assertEqual(screen.lines(), ("ABCD", ""))
                self.assertEqual((screen.row, screen.column, screen.wrap_pending), (0, 3, True))
                screen.feed("E\r\nF")
                self.assertEqual(screen.lines(), ("E", "F"))
                self.assertEqual(screen.alternate_scrolls, 1)
                screen.feed("\x1b[2;1H\x1b[2K\x1b[2J\x1b[?1049l")
                expected_history = ("prior shell history", "ABCD", "E") if archiving \
                    else ("prior shell history",)
                self.assertEqual(screen.primary_state(),
                                 (original[0], expected_history, *original[2:]))
                self.assertEqual(screen.erase_display_count, 1)
        screen = TerminalScreen(4, 2, archive_ed2=False)
        screen.feed("\x1b[?1049h\x1b[31me\u0301界Z\x1b[0m")
        self.assertEqual(screen.lines(), ("e\u0301界Z", ""))
        self.assertEqual((screen.row, screen.column, screen.wrap_pending), (0, 3, True))
        screen.feed("X")
        self.assertEqual(screen.lines(), ("e\u0301界Z", "X"))
        screen = TerminalScreen(4, 2, archive_ed2=False, eager_wrap=True)
        screen.feed("\x1b[?1049h\x1b[2;1HABCD")
        self.assertEqual(screen.alternate_scrolls, 1)

    def test_open_close_preserves_primary_screen_cursor_and_prior_history(self):
        for archiving in (False, True):
            for eager in (False, True):
                with self.subTest(archiving=archiving, eager=eager), \
                        _observed(archive_ed2=archiving, eager_wrap=eager,
                                  reader=lambda before_read=None: "q") as observed:
                    _, screen, original, terminal, _ = observed
                    view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                    self._assert_restored(screen, original)
                    self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))
                    self.assertFalse(terminal.input_stream.closed)
                    self.assertFalse(terminal.output_stream.closed)

    def test_paging_close_does_not_archive_loading_or_previous_pages(self):
        source = b"".join(b'{"content":"page-' + str(i).encode() + b'"}\n'
                          for i in range(1, 9))
        for archiving in (False, True):
            for eager in (False, True):
                frames = []
                keys = iter((" ", "q"))
                def reader(before_read=None):
                    frames.append(screen.lines())
                    return next(keys)
                with self.subTest(archiving=archiving, eager=eager), \
                        _observed(archive_ed2=archiving, eager_wrap=eager,
                                  reader=reader) as observed:
                    _, screen, original, terminal, _ = observed
                    view_jsonl(source, _SPEC, terminal=terminal, transient_only=True)
                    self.assertEqual(len(frames), 2)
                    self.assertNotEqual(frames[0], frames[1])
                    self.assertIn("page-1", "\n".join(frames[0]))
                    self._assert_restored(screen, original)

    def test_repeated_owned_sessions_preserve_the_same_primary_and_history(self):
        for archiving in (False, True):
            with self.subTest(archiving=archiving), \
                    _observed(archive_ed2=archiving,
                              reader=lambda before_read=None: "q") as observed:
                _, screen, original, terminal, _ = observed
                for _ in range(3):
                    view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                    self._assert_restored(screen, original)
                self.assertEqual(terminal.output_stream.getvalue().count(_ENTER), 3)
                self.assertEqual(terminal.output_stream.getvalue().count(_LEAVE), 3)

    def test_physical_geometry_caps_stale_overrides_and_honors_smaller_overrides(self):
        cases = (({}, (20, 6)),
                 ({"COLUMNS": "120", "LINES": "40"}, (20, 6)),
                 ({"COLUMNS": "12", "LINES": "4"}, (12, 4)),
                 ({"COLUMNS": "12", "LINES": "40"}, (12, 6)),
                 ({"COLUMNS": "bad", "LINES": "0"}, (20, 6)))
        for overrides, expected in cases:
            with self.subTest(overrides=overrides), \
                    _observed(20, 6, archive_ed2=True, overrides=overrides) as observed:
                host, screen, original, _, size = observed
                with host:
                    self.assertEqual(host.terminal_size(), expected)
                    size.assert_called_with(29)
                    host.present("VISIBLE\n" + "x" * 120 + "\n" + "tail\n" * 40)
                    self.assertIn("VISIBLE", screen.lines()[0])
                    self.assertEqual(screen.alternate_scrolls, 0)
                self._assert_restored(screen, original)

    def test_physical_width_below_renderer_minimum_clips_ansi_and_unicode_without_scrolling(self):
        fixtures = ((1, "e\u0301"), (2, "e\u0301X"),
                    (5, "e\u0301界ZX"), (11, "e\u0301界ZX012345"))
        for columns, first in fixtures:
            for archiving in (False, True):
                for eager in (False, True):
                    with self.subTest(columns=columns, archiving=archiving, eager=eager), \
                            _observed(columns, 4, archive_ed2=archiving,
                                      eager_wrap=eager,
                                      reader=lambda before_read=None: "q") as observed:
                        host, screen, original, terminal, _ = observed
                        with host:
                            host.present("\x1b[31m" + first + "\x1b[0m\n" +
                                         "0123456789ABC\nshort\n" + "X" * columns + "\nEXCESS")
                            self.assertEqual(screen.lines()[0], first)
                            self.assertEqual(screen.alternate_scrolls, 0)
                            self.assertEqual(tuple(screen.history), original[1])
                        self._assert_restored(screen, original)
                        # Exercise the renderer's existing minimum dimensions
                        # through the real owned adapter at the smaller TTY cap.
                        view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                        self._assert_restored(screen, original)

    def test_resize_and_shorter_frames_clear_all_physical_stale_rows(self):
        for archiving in (False, True):
            with self.subTest(archiving=archiving), \
                    _observed(16, 6, archive_ed2=archiving) as observed:
                host, screen, original, _, _ = observed
                with host:
                    host.present("\n".join("old-row-" + str(i) for i in range(6)))
                    self.assertIn("old-row-4", screen.lines())
                    screen.resize(12, 4)
                    self.assertEqual(host.terminal_size(), (12, 4))
                    host.present("short\nsecond")
                    self.assertEqual(screen.lines(), ("short", "second", "", ""))
                    screen.resize(20, 7)
                    self.assertEqual(host.terminal_size(), (20, 7))
                    host.present("after")
                    self.assertEqual(screen.lines(), ("after", "", "", "", "", "", ""))
                    primary = tuple(TerminalScreen._line(row) for row in screen.primary)
                self._assert_restored(screen, (primary, original[1], *original[2:]))

    def test_eof_and_render_failure_restore_without_archiving_viewer_rows(self):
        from jsonl_viewer import engine
        for archiving in (False, True):
            for failure in (None, RuntimeError("frame rendering failed")):
                def eof(before_read=None):
                    raise EOFError
                with self.subTest(archiving=archiving, failure=type(failure).__name__), \
                        _observed(archive_ed2=archiving, reader=eof) as observed:
                    _, screen, original, terminal, _ = observed
                    if failure is None:
                        view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                    else:
                        with mock.patch.object(engine, "render_frame", side_effect=failure), \
                                self.assertRaisesRegex(RuntimeError, "frame rendering failed"):
                            view_jsonl(_SOURCE, _SPEC, terminal=terminal, transient_only=True)
                    self._assert_restored(screen, original)
                    self.assertTrue(terminal.output_stream.getvalue().endswith(_LEAVE))


if __name__ == "__main__":
    unittest.main()
