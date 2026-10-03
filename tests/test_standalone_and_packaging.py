"""Standalone command and source-distribution boundary tests."""

from __future__ import annotations

import ast
import errno
import io
import importlib.util
import json
import os
import re
import select as select_module
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from jsonl_viewer import _standalone
from jsonl_viewer._input import MAX_SOURCE_BYTES
from jsonl_viewer._standalone import _TerminalHost, _read_bounded


ROOT = Path(__file__).resolve().parents[1]
_PRESENT = b"\x1b[H\x1b[2J"
_HELP = "? help • q close".encode()


class _TerminalBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 17


class _FlushControlledBuffer(_TerminalBuffer):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_flush = False

    def flush(self) -> None:
        if self.fail_next_flush:
            self.fail_next_flush = False
            raise RuntimeError("flush failed")
        super().flush()


class _RawScript:
    def __init__(
        self,
        values: bytes | list[bytes | BaseException],
        readiness: list[bool],
    ) -> None:
        self.values: list[bytes | BaseException]
        if isinstance(values, bytes):
            self.values = [bytes((value,)) for value in values]
        else:
            self.values = list(values)
        self.readiness = list(readiness)
        self.read_sizes: list[int] = []
        self.select_timeouts: list[float] = []

    def read(self, descriptor: int, size: int) -> bytes:
        if descriptor != 17:
            raise AssertionError(f"unexpected descriptor {descriptor}")
        self.read_sizes.append(size)
        if not self.values:
            raise AssertionError("unexpected raw read")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        if len(value) > size:
            raise AssertionError(f"raw read {size} cannot return {len(value)} bytes")
        return value

    def select(
        self,
        read: list[int],
        write: list[int],
        error: list[int],
        timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        if read != [17] or write or error:
            raise AssertionError("unexpected select arguments")
        if timeout > 0.02:
            if timeout != 0.5:
                raise AssertionError(f"unexpected idle timeout {timeout}")
            return ([17], [], [])
        self.select_timeouts.append(timeout)
        if not self.readiness:
            raise AssertionError("unexpected escape-sequence select")
        return ([17] if self.readiness.pop(0) else [], [], [])

    def assert_consumed(self, case: unittest.TestCase) -> None:
        case.assertEqual(self.values, [])
        case.assertEqual(self.readiness, [])
        case.assertTrue(all(size == 1 for size in self.read_sizes))
        case.assertTrue(all(0 < timeout <= 0.020001 for timeout in self.select_timeouts))


class StandaloneTests(unittest.TestCase):
    def _scripted_event(
        self,
        script: _RawScript,
        *,
        frames: tuple[str, ...] = ("PRIOR FRAME",),
        output: _TerminalBuffer | None = None,
    ) -> tuple[str | None, str, str, _TerminalHost, mock.Mock, mock.Mock]:
        input_stream = _TerminalBuffer()
        output_stream = output if output is not None else _TerminalBuffer()
        fake_termios = mock.Mock()
        fake_termios.TCSADRAIN = 1
        fake_termios.VINTR = 0
        fake_termios.VEOF = 1
        fake_termios.tcgetattr.return_value = [0, 0, 0, 0, 0, 0, [b"\x03", b"\x04"]]
        fake_tty = mock.Mock()
        host = _TerminalHost(input_stream, output_stream, no_color=True)
        with (
            mock.patch.object(_standalone, "termios", fake_termios),
            mock.patch.object(_standalone, "tty", fake_tty),
            mock.patch.object(_standalone.os, "read", side_effect=script.read),
            mock.patch.object(
                _standalone.time,
                "monotonic",
                return_value=100.0,
            ),
            mock.patch.object(
                _standalone.select,
                "select",
                side_effect=script.select,
            ),
        ):
            with host:
                for frame in frames:
                    host.present(frame)
                event = host.read_event()
                before_restore = output_stream.getvalue()
        return (
            event,
            before_restore,
            output_stream.getvalue(),
            host,
            fake_termios,
            fake_tty,
        )

    def _start_pty(
        self,
        path: Path,
    ) -> tuple[subprocess.Popen[bytes], int]:
        if not hasattr(os, "openpty") or _standalone.termios is None:
            self.skipTest("POSIX PTY support is unavailable")
        try:
            import fcntl
            import termios as system_termios
        except ImportError:
            self.skipTest("POSIX terminal control is unavailable")

        master, slave = os.openpty()
        self._pty_original_attributes = system_termios.tcgetattr(slave)
        fcntl.ioctl(
            slave,
            system_termios.TIOCSWINSZ,
            struct.pack("HHHH", 24, 160, 0, 0),
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["TERM"] = "xterm-256color"
        environment.pop("NO_COLOR", None)
        command = [
            sys.executable,
            "-m",
            "jsonl_viewer",
            str(path),
            "--session",
            "pty-session",
            "--conversation",
            "pty-conversation",
            "--agent",
            "pty-agent",
            "--no-color",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                close_fds=True,
            )
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        return process, master

    def _pty_read_until(
        self,
        master: int,
        output: bytearray,
        needle: bytes,
        *,
        start: int = 0,
        timeout: float = 3.0,
    ) -> int:
        deadline = time.monotonic() + timeout
        while True:
            found = output.find(needle, start)
            if found >= 0:
                return found
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(f"PTY timeout waiting for {needle!r}: {bytes(output)!r}")
            ready, _, _ = select_module.select([master], [], [], remaining)
            if not ready:
                self.fail(f"PTY timeout waiting for {needle!r}: {bytes(output)!r}")
            try:
                current = os.read(master, 65_536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    self.fail(f"PTY closed before {needle!r}: {bytes(output)!r}")
                raise
            if not current:
                self.fail(f"PTY reached EOF before {needle!r}: {bytes(output)!r}")
            output.extend(current)

    def _pty_frame_containing(
        self,
        master: int,
        output: bytearray,
        marker: bytes,
        *,
        start: int,
    ) -> tuple[bytes, int]:
        marker_index = self._pty_read_until(
            master,
            output,
            marker,
            start=start,
        )
        footer_index = self._pty_read_until(
            master, output, _HELP, start=marker_index,
        )
        frame_start = output.rfind(_PRESENT, 0, footer_index)
        if frame_start < 0:
            self.fail(f"frame marker has no presentation prefix: {bytes(output)!r}")
        self.assertIn(marker, output[frame_start:footer_index])
        frame_end = footer_index + len(_HELP)
        return bytes(output[frame_start + len(_PRESENT) : frame_end]), frame_end

    def _pty_write(self, master: int, value: bytes) -> None:
        remaining = memoryview(value)
        while remaining:
            written = os.write(master, remaining)
            remaining = remaining[written:]

    def _pty_finish(
        self,
        process: subprocess.Popen[bytes],
        master: int,
        output: bytearray,
    ) -> None:
        process.wait(timeout=3)
        while select_module.select([master], [], [], 0)[0]:
            try:
                current = os.read(master, 65_536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not current:
                break
            output.extend(current)

    def _pty_cleanup(self, process: subprocess.Popen[bytes], master: int) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        os.close(master)

    def test_no_color_environment_overrides_a_capable_terminal(self) -> None:
        class TerminalBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

        output = TerminalBuffer()
        host = _TerminalHost(TerminalBuffer(), output, no_color=False)
        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            self.assertTrue(host.color_enabled())
        with mock.patch.dict(
            os.environ,
            {"TERM": "xterm-256color", "NO_COLOR": "1"},
            clear=True,
        ):
            self.assertFalse(host.color_enabled())

    def test_raw_host_decodes_physical_events_without_viewer_actions(self) -> None:
        cases = [(char.encode(), "text\t" + char) for char in "/gqjkb nNmh?c개요😀e\u0301"]
        cases += [
            (b"\x15", "key\tctrl_u"), (b"\x01", "key\tctrl_a"),
            (b"\x05", "key\tctrl_e"), (b"\x0b", "key\tctrl_k"),
            (b"\x17", "key\tctrl_w"), (b"\x08", "key\tbackspace"),
            (b"\x7f", "key\tbackspace"), (b"\r", "key\tenter"),
            (b"\n", "key\tenter"), (b"\x03", "key\tinterrupt"),
            (b"\x04", "key\teof"), (b"\t", "key\tunknown"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                script = _RawScript(raw, [])
                event, before, complete, _, termios, tty = self._scripted_event(script)
                self.assertEqual(event, expected)
                self.assertEqual(before, "\x1b[?1049h\x1b[?25l\x1b[H\x1b[2JPRIOR FRAME")
                self.assertTrue(complete.endswith("\x1b[?25h\x1b[?1049l"))
                tty.setcbreak.assert_called_once_with(17)
                termios.tcsetattr.assert_called_once_with(17, 1, termios.tcgetattr.return_value)
                script.assert_consumed(self)

    def test_escape_sequences_are_bounded_drained_and_never_leak(self) -> None:
        cases = {
            b"[A": "up", b"OB": "down", b"[C": "right", b"OD": "left",
            b"[H": "home", b"OF": "end", b"[3~": "delete",
            b"[5~": "page_up", b"[6~": "page_down",
            b"[Z": "unknown_escape", b"OP": "unknown_escape",
            b"[" + b"0" * 30 + b"Z": "unknown_escape",
            b"[" + b"0" * 31 + b"Z": "unknown_escape",
        }
        for suffix, key in cases.items():
            with self.subTest(suffix=suffix):
                script = _RawScript(b"\x1b" + suffix + b"q", [True] * len(suffix))
                event, before, _, _, _, _ = self._scripted_event(script)
                self.assertEqual(event, "key\t" + key)
                self.assertEqual(script.values, [b"q"])
                script.values.clear()
                script.assert_consumed(self)
                self.assertNotIn("Search", before)
        for suffix, readiness, key in [(b"", [False], "escape"), (b"[", [True, False], "unknown_escape")]:
            script = _RawScript(b"\x1b" + suffix, readiness)
            self.assertEqual(self._scripted_event(script)[0], "key\t" + key)
            script.assert_consumed(self)

    def test_escape_drain_ceiling_fails_closed_and_restores(self) -> None:
        script = _RawScript(b"\x1b[" + b"0" * 4096 + b"q", [True] * 4097)
        output = _TerminalBuffer()
        with self.assertRaisesRegex(ValueError, "drain bound"):
            self._scripted_event(script, output=output)
        self.assertEqual(script.values, [b"q"])
        self.assertTrue(output.getvalue().endswith("\x1b[?25h\x1b[?1049l"))

    def test_eof_interrupt_and_read_failure_restore_terminal(self) -> None:
        for values, expected in [([b""], None), ([KeyboardInterrupt()], "key\tinterrupt")]:
            script = _RawScript(values, [])
            event, _, complete, _, _, _ = self._scripted_event(script)
            self.assertEqual(event, expected)
            self.assertTrue(complete.endswith("\x1b[?25h\x1b[?1049l"))
            script.assert_consumed(self)
        for values, error in [([OSError("read failed")], OSError), ([b"\xe2", b""], UnicodeDecodeError)]:
            output = _TerminalBuffer()
            with self.assertRaises(error):
                self._scripted_event(_RawScript(values, []), output=output)
            self.assertTrue(output.getvalue().endswith("\x1b[?25h\x1b[?1049l"))

    def test_plain_host_returns_literal_bounded_line_transport(self) -> None:
        values = ["esc", "/content alpha", " / content 개요 ", "g 2", "q", "\tq\t", ""]
        host = _TerminalHost(io.StringIO("\n".join(values) + "\n"), io.StringIO(), no_color=True)
        for value in values:
            self.assertEqual(host.read_event(), "line\t" + value)
        self.assertIsNone(host.read_event())
        for count in [8192, 8193, 20000]:
            with self.subTest(count=count):
                host = _TerminalHost(io.StringIO("x" * count + "\nq\n"), io.StringIO(), no_color=True)
                expected = "line\t" + "x" * count if count == 8192 else "key\tunknown"
                self.assertEqual(host.read_event(), expected)
                self.assertEqual(host.read_event(), "line\tq")
        host = _TerminalHost(io.StringIO("x" * 65537), io.StringIO(), no_color=True)
        with self.assertRaisesRegex(ValueError, "drain bound"):
            host.read_event()

    def test_present_flush_failure_propagates_without_retaining_view_state(self) -> None:
        output = _FlushControlledBuffer()
        host = _TerminalHost(io.StringIO(), output, no_color=True)
        host.present("FRAME A")
        output.fail_next_flush = True
        with self.assertRaisesRegex(RuntimeError, "flush failed"):
            host.present("FRAME B")
        self.assertFalse(hasattr(host, "_last_presented_frame"))
        host.close_view()
        host.close_view()

    def test_ordinary_line_search_then_escape_clears_and_closes(self) -> None:
        source = (
            b'{"timestamp":"1","request_type":"response",'
            b'"content":"alpha one"}\n'
            b'{"timestamp":"2","request_type":"response",'
            b'"content":"alpha two"}\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["NO_COLOR"] = "1"
            environment["COLUMNS"] = "200"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jsonl_viewer",
                    str(path),
                    "--session",
                    "line-session",
                    "--conversation",
                    "line-conversation",
                    "--agent",
                    "line-agent",
                ],
                input="/ alpha\nesc\nesc\nesc\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("1/2 occurrences • Search all text='alpha'", completed.stdout)
            self.assertIn("Search cleared.", completed.stdout)
            self.assertNotIn("Unknown event", completed.stdout)
            self.assertEqual(path.read_bytes(), source)

    def test_raw_pty_draft_escape_restores_exact_frame_and_next_command(self) -> None:
        source = (
            b'{"timestamp":"1","request_type":"response",'
            b'"content":"alpha one"}\n'
            b'{"timestamp":"2","request_type":"response",'
            b'"content":"alpha two"}\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            process, master = self._start_pty(path)
            output = bytearray()
            try:
                _, initial_end = self._pty_frame_containing(
                    master,
                    output,
                    b'"content": "alpha one"',
                    start=0,
                )
                self._pty_write(master, b"m")
                prior, prior_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Switched to Verbose mode.",
                    start=initial_end,
                )

                self._pty_write(master, b"/")
                prompt = self._pty_read_until(
                    master,
                    output,
                    b"Search query: ",
                    start=prior_end,
                )
                self._pty_write(master, b"partial")
                self._pty_write(master, b"\x1b")
                restored, restored_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Switched to Verbose mode.",
                    start=prompt,
                )
                self.assertEqual(restored, prior)

                prompt_segment = bytes(output[prior_end:restored_end])
                self.assertNotIn(b"\x1b[?25h", prompt_segment)
                self.assertNotIn(b"\x1b[?25l", prompt_segment)

                self._pty_write(master, b"\x1b[B")
                moved, moved_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Last record.",
                    start=restored_end,
                )
                self.assertIn("READ ONLY • VERBOSE".encode(), moved)
                self._pty_write(master, b"q")
                self._pty_read_until(
                    master,
                    output,
                    b"\x1b[?25h\x1b[?1049l",
                    start=moved_end,
                )
                self._pty_finish(process, master, output)

                self.assertEqual(process.returncode, 0)
                self.assertIn(b"\x1b[?1049h\x1b[?25l", output)
                self.assertTrue(output.endswith(_PRESENT + b"\x1b[?25h\x1b[?1049l"))
                self.assertNotIn(b"Unknown event", output)
                self.assertEqual(path.read_bytes(), source)
            finally:
                self._pty_cleanup(process, master)

    def test_raw_pty_query_escape_preserves_search_and_main_escape_sequence(
        self,
    ) -> None:
        source = (
            b'{"timestamp":"1","request_type":"response",'
            b'"content":"alpha one"}\n'
            b'{"timestamp":"2","request_type":"response",'
            b'"content":"alpha two"}\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            process, master = self._start_pty(path)
            output = bytearray()
            try:
                _, initial_end = self._pty_frame_containing(
                    master,
                    output,
                    b'"content": "alpha one"',
                    start=0,
                )
                self._pty_write(master, b"/")
                field_prompt = self._pty_read_until(
                    master,
                    output,
                    b"Search query: ",
                    start=initial_end,
                )
                query_prompt = field_prompt
                self._pty_write(master, b"alpha\r")
                active, active_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search all text='alpha'",
                    start=query_prompt,
                )
                self.assertIn(b"1/2", active)

                self._pty_write(master, b"/")
                second_field = self._pty_read_until(
                    master,
                    output,
                    b"Search query: ",
                    start=active_end,
                )
                second_query = second_field
                self._pty_write(master, b"replacement")
                self._pty_write(master, b"\x1b")
                restored, restored_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search all text='alpha'",
                    start=second_query,
                )
                self.assertEqual(restored, active)

                self._pty_write(master, b"n")
                next_match, next_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search all text='alpha'",
                    start=restored_end,
                )
                self.assertIn(b"2/2", next_match)

                self._pty_write(master, b"\x1b")
                _, cleared_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search cleared.",
                    start=next_end,
                )
                self._pty_write(master, b"\x1b")
                ordinary, ordinary_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Record 2/2",
                    start=cleared_end,
                )
                self.assertNotIn(b"Search all text=", ordinary)
                self.assertNotIn(b"Search cleared.", ordinary)
                self._pty_write(master, b"\x1b")
                self._pty_read_until(
                    master,
                    output,
                    b"\x1b[?25h\x1b[?1049l",
                    start=ordinary_end,
                )
                self._pty_finish(process, master, output)

                self.assertEqual(process.returncode, 0)
                self.assertTrue(output.endswith(_PRESENT + b"\x1b[?25h\x1b[?1049l"))
                self.assertNotIn(b"Unknown event", output)
                self.assertEqual(path.read_bytes(), source)
            finally:
                self._pty_cleanup(process, master)

    def test_raw_pty_ctrl_u_unicode_goto_cancel_and_termios_restoration(self) -> None:
        import json
        import termios as system_termios

        source = b"".join((json.dumps({"content": "개요 " + str(i)}, ensure_ascii=False) + "\n").encode() for i in range(7))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            process, master = self._start_pty(path)
            original = self._pty_original_attributes
            output = bytearray()
            try:
                _, initial_end = self._pty_frame_containing(master, output, b"Record 1/7", start=0)
                raw = system_termios.tcgetattr(master)
                self.assertFalse(raw[3] & system_termios.ICANON)
                self.assertFalse(raw[3] & system_termios.ECHO)
                self._pty_write(master, b"/wrong\x15" + "개요".encode() + b"\r")
                active, active_end = self._pty_frame_containing(master, output, "Search all text='개요'".encode(), start=initial_end)
                self.assertIn(b"1/7", active)
                self.assertNotIn(b"U0015", active)
                self.assertNotIn(b"wrong", active)
                self._pty_write(master, b"n")
                active, active_end = self._pty_frame_containing(master, output, "2/7 occurrences • Search all text='개요'".encode(), start=active_end)
                for draft, prompt in ((b"/partial", b"Search query: "), (b"g999", b"Go to source line: ")):
                    self._pty_write(master, draft)
                    prompt_start = self._pty_read_until(master, output, prompt, start=active_end)
                    self.assertFalse(system_termios.tcgetattr(master)[3] & system_termios.ICANON)
                    self._pty_write(master, b"\x1b")
                    restored, active_end = self._pty_frame_containing(master, output, "2/7 occurrences • Search all text='개요'".encode(), start=prompt_start)
                    self.assertEqual(restored, active)
                self._pty_write(master, b"q")
                self._pty_read_until(master, output, b"\x1b[?25h\x1b[?1049l", start=active_end)
                self._pty_finish(process, master, output)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(system_termios.tcgetattr(master), original)
                self.assertEqual(output.count(b"\x1b[?1049h"), 1)
                self.assertEqual(output.count(b"\x1b[?1049l"), 1)
                self.assertEqual(path.read_bytes(), source)
            finally:
                self._pty_cleanup(process, master)

    def test_raw_pty_full_text_unicode_preserves_cleanup(self) -> None:
        source = b"".join(
            (json.dumps({
                "content": "orientation",
                "payload": json.dumps({"items": [{"status": "Straße 개요"}]}),
            }) + "\n").encode()
            for _ in range(2)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            process, master = self._start_pty(path)
            import termios as system_termios

            original = self._pty_original_attributes
            output = bytearray()
            try:
                _, end = self._pty_frame_containing(master, output, b"Record 1/2", start=0)
                self._pty_write(master, b"/")
                prompt = self._pty_read_until(master, output, b"Search query: ", start=end)
                self._pty_write(master, "STRASSE 개요\r".encode())
                active, end = self._pty_frame_containing(
                    master, output, "1/2 occurrences • Search all text='STRASSE 개요'".encode(), start=prompt,
                )
                self.assertIn("READ ONLY • VERBOSE".encode(), active)
                self.assertIn('"status": "⟦Straße 개요⟧"'.encode(), active)
                self._pty_write(master, b"n")
                active, end = self._pty_frame_containing(
                    master, output, "2/2 occurrences • Search all text='STRASSE 개요'".encode(), start=end,
                )
                self._pty_write(master, b"/draft")
                prompt = self._pty_read_until(master, output, b"Search query: ", start=end)
                self._pty_write(master, b"\x1b")
                restored, end = self._pty_frame_containing(
                    master, output, "2/2 occurrences • Search all text='STRASSE 개요'".encode(), start=prompt,
                )
                self.assertEqual(restored, active)

                self._pty_write(master, b"/STRASSE\r")
                selected, end = self._pty_frame_containing(
                    master, output, b"Search all text='STRASSE'", start=end,
                )
                self.assertIn(b"1/2", selected)
                self._pty_write(master, b"N")
                selected, end = self._pty_frame_containing(
                    master, output, "2/2 occurrences • Search all text='STRASSE'".encode(), start=end,
                )
                self.assertIn(b"@ 2", selected)
                self._pty_write(master, b"q")
                self._pty_read_until(master, output, b"\x1b[?25h\x1b[?1049l", start=end)
                self._pty_finish(process, master, output)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(system_termios.tcgetattr(master), original)
                self.assertEqual(output.count(b"\x1b[?1049h"), 1)
                self.assertEqual(output.count(b"\x1b[?1049l"), 1)
                self.assertEqual(path.read_bytes(), source)
            finally:
                self._pty_cleanup(process, master)

    def test_cli_rejects_removed_searchable_field_option(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "jsonl_viewer", "-", "--session", "s",
             "--conversation", "c", "--agent", "a", "--searchable-field", "content"],
            input=b"{}\n", capture_output=True, check=False, timeout=5,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"unrecognized arguments: --searchable-field content", completed.stderr)

    def test_line_cli_searches_entire_phrase(self) -> None:
        source = (json.dumps({
            "content": "single", "payload": json.dumps({"items": [{"status": "two words"}]}),
        }) + "\n").encode()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            environment = os.environ.copy()
            environment.update({
                "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                "NO_COLOR": "1", "COLUMNS": "200",
            })
            completed = subprocess.run(
                [sys.executable, "-m", "jsonl_viewer", str(path),
                 "--session", "s", "--conversation", "c", "--agent", "a"],
                input="/ two words\n/ single\n/ payload.items[0].status two words\n/ content single\nq\n",
                capture_output=True, text=True, env=environment, check=False, timeout=5,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            for result in (
                "1/1 occurrences • Search all text='two words'", "1/1 occurrences • Search all text='single'",
                "0/0 occurrences • Search all text='payload.items[0].status two words'", "0/0 occurrences • Search all text='content single'",
            ):
                self.assertIn(result, completed.stdout)
            self.assertNotIn("\x1b", completed.stdout)
            self.assertNotIn("Unknown event", completed.stdout)
            self.assertEqual(path.read_bytes(), source)

    def test_plain_line_standalone_smoke_preserves_source(self) -> None:
        source = b'{"timestamp":"t","request_type":"request","content":"hello"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["NO_COLOR"] = "1"
            environment["COLUMNS"] = "200"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jsonl_viewer",
                    str(path),
                    "--session",
                    "standalone-session",
                    "--conversation",
                    "standalone-conversation",
                    "--conversation-label",
                    "Debate",
                    "--conversation-subject",
                    "Provider diagnostics",
                    "--agent",
                    "standalone-agent",
                ],
                input="q\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("READ ONLY", completed.stdout)
            self.assertIn("standalone-session", completed.stdout)
            self.assertIn(
                "Debate: standalone-conversation — Provider diagnostics",
                completed.stdout,
            )
            self.assertIn('"content": "hello"', completed.stdout)
            self.assertNotIn("\x1b", completed.stdout)
            self.assertEqual(path.read_bytes(), source)

    def test_stdin_snapshot_renders_once_then_closes_on_eof(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["NO_COLOR"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "jsonl_viewer",
                "-",
                "--session",
                "stdin-session",
                "--conversation",
                "stdin-conversation",
                "--agent",
                "stdin-agent",
            ],
            input=b'{"content":"from stdin"}\n',
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        output = completed.stdout.decode()
        self.assertIn("stdin-session", output)
        self.assertIn("Conversation: stdin-conversation", output)
        self.assertNotIn("Conversation: stdin-conversation —", output)
        self.assertIn('"content": "from stdin"', output)
        self.assertNotIn("\x1b", output)

    def test_bounded_reader_requests_only_one_byte_beyond_snapshot_limit(self) -> None:
        handle = mock.Mock(spec=io.BytesIO)
        handle.read.return_value = b"bounded"
        self.assertEqual(_read_bounded(handle), b"bounded")
        handle.read.assert_called_once_with(MAX_SOURCE_BYTES + 1)

    def test_interactive_terminal_restores_after_hosted_failure(self) -> None:
        class TerminalBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

            def fileno(self) -> int:
                return 17

        input_stream = TerminalBuffer()
        output_stream = TerminalBuffer()
        fake_termios = mock.Mock()
        fake_termios.TCSADRAIN = 1
        fake_termios.VINTR = 0
        fake_termios.VEOF = 1
        fake_termios.tcgetattr.return_value = [0, 0, 0, 0, 0, 0, [b"\x03", b"\x04"]]
        fake_tty = mock.Mock()
        with (
            mock.patch.object(_standalone, "termios", fake_termios),
            mock.patch.object(_standalone, "tty", fake_tty),
            self.assertRaisesRegex(RuntimeError, "hosted failure"),
        ):
            with _TerminalHost(input_stream, output_stream, no_color=True):
                raise RuntimeError("hosted failure")

        fake_tty.setcbreak.assert_called_once_with(17)
        fake_termios.tcsetattr.assert_called_once_with(17, 1, fake_termios.tcgetattr.return_value)
        output = output_stream.getvalue()
        self.assertTrue(output.startswith("\x1b[?1049h\x1b[?25l"))
        self.assertTrue(output.endswith("\x1b[?25h\x1b[?1049l"))

    def test_missing_source_is_content_safe_cli_error(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "jsonl_viewer",
                "/definitely/missing/viewer-input.jsonl",
                "--session",
                "s",
                "--conversation",
                "c",
                "--agent",
                "a",
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("FileNotFoundError", completed.stderr)


    def test_idle_poll_preserves_partial_utf8_and_distinguishes_eof(self):
        host = _TerminalHost(_TerminalBuffer(), _TerminalBuffer(), no_color=True)
        host._interactive = True
        host._descriptor = 17
        encoded = "界".encode()
        ready = ([17], [], [])
        idle = ([], [], [])
        with mock.patch.object(_standalone.time, "monotonic", return_value=100.0), \
             mock.patch.object(_standalone.select, "select", side_effect=[
            idle, ready, idle, ready, ready, ready,
        ]) as poll, mock.patch.object(_standalone.os, "read", side_effect=[
            encoded[:1], encoded[1:2], encoded[2:], b"",
        ]) as read:
            self.assertEqual(host.read_event(), "idle")
            self.assertEqual(host.read_event(), "idle")
            self.assertEqual(host.read_event(), "text\t界")
            self.assertIsNone(host.read_event())
        self.assertEqual(read.call_count, 4)
        self.assertTrue(all(call.args == ([17], [], [], 0.5) for call in poll.call_args_list))

    def test_idle_deadline_is_not_restarted_by_partial_utf8(self):
        host = _TerminalHost(_TerminalBuffer(), _TerminalBuffer(), no_color=True)
        host._interactive = True
        host._descriptor = 17
        ready = ([17], [], [])
        with mock.patch.object(_standalone.time, "monotonic", side_effect=[100.0, 100.1, 100.4]), \
             mock.patch.object(_standalone.select, "select", side_effect=[ready, ([], [], [])]) as poll, \
             mock.patch.object(_standalone.os, "read", return_value=b"\xe7"):
            self.assertEqual(host.read_event(), "idle")
        self.assertAlmostEqual(poll.call_args_list[0].args[3], 0.4)
        self.assertAlmostEqual(poll.call_args_list[1].args[3], 0.1)

    def test_ordinary_line_host_does_not_poll_or_emit_idle(self):
        host = _TerminalHost(io.StringIO("J\nK\n"), io.StringIO(), no_color=True)
        with mock.patch.object(_standalone.select, "select") as poll:
            self.assertEqual(host.read_event(), "line\tJ")
            self.assertEqual(host.read_event(), "line\tK")
            self.assertIsNone(host.read_event())
        poll.assert_not_called()

    def test_raw_pty_idle_blink_split_utf8_and_terminal_restoration(self):
        source = '{"content":{"first":"界","second":2}}\n'.encode()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            process, master = self._start_pty(path)
            import termios as system_termios
            output = bytearray()
            try:
                first, end = self._pty_frame_containing(master, output, b"Record 1/1", start=0)
                hidden, end = self._pty_frame_containing(master, output, b"Record 1/1", start=end)
                self.assertIn(b"^", first)
                self.assertNotIn(b"^", hidden)
                self._pty_write(master, b"/")
                end = self._pty_read_until(master, output, b"Search query: ", start=end)
                encoded = "界".encode()
                self._pty_write(master, encoded[:1])
                # Let the raw poll expire between UTF-8 bytes while a prompt is open.
                time.sleep(0.6)
                self._pty_write(master, encoded[1:])
                end = self._pty_read_until(master, output, b"Search query: " + encoded, start=end)
                self._pty_write(master, b"\r")
                _, end = self._pty_frame_containing(master, output, b"1/1 occurrences", start=end)
                self._pty_write(master, b"\x04")
                self._pty_read_until(master, output, b"\x1b[?1049l", start=end)
                self._pty_finish(process, master, output)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(system_termios.tcgetattr(master), self._pty_original_attributes)
                self.assertEqual(output.count(b"\x1b[?1049h"), 1)
                self.assertEqual(output.count(b"\x1b[?1049l"), 1)
                self.assertEqual(path.read_bytes(), source)
            finally:
                self._pty_cleanup(process, master)


class PackagingBoundaryTests(unittest.TestCase):
    def _modules(self) -> dict[str, Path]:
        package = ROOT / "src" / "jsonl_viewer"
        modules: dict[str, Path] = {}
        for path in sorted(package.rglob("*.py")):
            parts = list(path.relative_to(package).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            name = "jsonl_viewer" + ("." + ".".join(parts) if parts else "")
            modules[name] = path
        return modules

    def test_metadata_has_no_runtime_or_development_dependency(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]
        self.assertEqual(project["name"], "jsonl-viewer")
        self.assertEqual(project["version"], "0.5.2")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["license"], "MIT")
        self.assertNotIn("optional-dependencies", project)
        self.assertEqual(
            project["scripts"],
            {"jsonl-viewer": "jsonl_viewer._standalone:main"},
        )

    def test_inventory_edges_cycles_and_story_isolation(self) -> None:
        modules = self._modules()
        graph: dict[str, set[str]] = {module: set() for module in modules}
        external: set[str] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            package = (
                module if path.name == "__init__.py" else module.rpartition(".")[0]
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    dependency = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""),
                        package,
                    )
                    if dependency in modules:
                        graph[module].add(dependency)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in modules:
                            graph[module].add(alias.name)
                        elif alias.name.startswith("story_writing_agents"):
                            external.add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("story_writing_agents"):
                        external.add(node.module)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"internal cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)
        self.assertEqual(len(modules), 10)
        self.assertEqual(sum(len(value) for value in graph.values()), 18)
        self.assertEqual(external, set())
        facade = (ROOT / "src/jsonl_viewer/__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("_standalone", facade)

        index = (ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        indexed = set(re.findall(r"^### `([^`]+)`$", index, flags=re.MULTILINE))
        self.assertEqual(indexed, set(modules))
        self.assertIn("Importable production units indexed: 10.", index)
        self.assertIn("Direct internal dependency edges indexed: 18.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertTrue(
            (
                ROOT / ".codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md"
            ).is_file()
        )

    def test_mit_license_and_intentional_source_tree(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Kim's Developers Group", license_text)
        package_files = {
            path.relative_to(ROOT / "src/jsonl_viewer").as_posix()
            for path in (ROOT / "src/jsonl_viewer").iterdir()
            if path.is_file()
        }
        self.assertEqual(
            package_files,
            {
                "__init__.py",
                "__main__.py",
                "_input.py",
                "_json.py",
                "_model.py",
                "_render.py",
                "_search.py",
                "_standalone.py",
                "contracts.py",
                "engine.py",
                "py.typed",
            },
        )


if __name__ == "__main__":
    unittest.main()
