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
        with _acquired(terminal) as (host, _, _, _, _):
            with host, mock.patch.object(_terminal.os, "get_terminal_size", return_value=os.terminal_size((88, 22))) as size:
                with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
                    self.assertEqual(host.terminal_size(), (88, 22))
                size.assert_called_once_with(29)
                for overrides, expected in (({"COLUMNS": "120", "LINES": "24"}, (120, 24)),
                                            ({"COLUMNS": "120"}, (120, 22)),
                                            ({"LINES": "24"}, (88, 24)),
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


if __name__ == "__main__":
    unittest.main()
