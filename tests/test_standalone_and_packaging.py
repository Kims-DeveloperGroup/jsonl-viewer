"""Standalone command and source-distribution boundary tests."""

from __future__ import annotations

import ast
import errno
import io
import importlib.util
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
from types import SimpleNamespace
from unittest import mock

from jsonl_viewer import _standalone
from jsonl_viewer._input import MAX_SOURCE_BYTES
from jsonl_viewer._standalone import _TerminalHost, _read_bounded


ROOT = Path(__file__).resolve().parents[1]
_PRESENT = b"\x1b[H\x1b[2J"
_HELP = "h help • q close".encode()


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


class _HideFailingBuffer(_TerminalBuffer):
    def write(self, value: str) -> int:
        if value == "\x1b[?25l":
            raise RuntimeError("hide failed")
        return super().write(value)


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
        self.select_timeouts.append(timeout)
        if not self.readiness:
            raise AssertionError("unexpected escape-sequence select")
        return ([17] if self.readiness.pop(0) else [], [], [])

    def assert_consumed(self, case: unittest.TestCase) -> None:
        case.assertEqual(self.values, [])
        case.assertEqual(self.readiness, [])
        case.assertTrue(all(size == 1 for size in self.read_sizes))
        case.assertTrue(all(0 < timeout <= 0.02 for timeout in self.select_timeouts))


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
        fake_termios.tcgetattr.return_value = ["saved"]
        fake_tty = mock.Mock()
        host = _TerminalHost(input_stream, output_stream, no_color=True)
        with (
            mock.patch.object(_standalone, "termios", fake_termios),
            mock.patch.object(_standalone, "tty", fake_tty),
            mock.patch.object(_standalone.os, "read", side_effect=script.read),
            mock.patch.object(
                _standalone.os,
                "times",
                return_value=SimpleNamespace(elapsed=100.0),
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
            "--searchable-field",
            "content",
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
        frame_start = output.rfind(_PRESENT, start, marker_index + len(marker))
        if frame_start < 0:
            self.fail(f"frame marker has no presentation prefix: {bytes(output)!r}")
        footer_index = self._pty_read_until(
            master,
            output,
            _HELP,
            start=marker_index,
        )
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

    def test_raw_field_escape_redraws_only_last_frame_and_returns_next_event(
        self,
    ) -> None:
        script = _RawScript(b"/partial\x1bq", [False])
        event, before_restore, complete, host, fake_termios, fake_tty = (
            self._scripted_event(script, frames=("FRAME A", "FRAME B"))
        )

        self.assertEqual(event, "close")
        self.assertEqual(before_restore.count("\x1b[H\x1b[2JFRAME A"), 1)
        self.assertEqual(before_restore.count("\x1b[H\x1b[2JFRAME B"), 2)
        redraw = before_restore.rfind("\x1b[H\x1b[2JFRAME B")
        self.assertEqual(
            before_restore[redraw:],
            "\x1b[H\x1b[2JFRAME B",
        )
        prompt = before_restore.index("Search field: ")
        hidden = before_restore.rfind("\x1b[?25l", prompt, redraw)
        self.assertGreater(hidden, prompt)
        self.assertNotIn("partial", before_restore[redraw:])
        self.assertTrue(complete.endswith("\x1b[?25h\x1b[?1049l"))
        self.assertIsNone(host._last_presented_frame)
        fake_tty.setcbreak.assert_called_once_with(17)
        fake_termios.tcsetattr.assert_called_once_with(17, 1, ["saved"])
        script.assert_consumed(self)

        empty_host = _RawScript(b"/\x1bq", [False])
        event, before_restore, _, host, _, _ = self._scripted_event(
            empty_host,
            frames=(),
        )
        self.assertEqual(event, "close")
        self.assertNotIn("\x1b[H\x1b[2J", before_restore)
        self.assertIsNone(host._last_presented_frame)
        empty_host.assert_consumed(self)

    def test_raw_query_escape_discards_partial_draft_and_preserves_active_frame(
        self,
    ) -> None:
        active = "ACTIVE SEARCH FRAME • Search content='alpha' • 1/2"
        script = _RawScript(b"/content\rreplacement\x1bn", [False])

        event, before_restore, _, _, _, _ = self._scripted_event(
            script,
            frames=(active,),
        )

        self.assertEqual(event, "next_match")
        self.assertIn("Search field: content", before_restore)
        self.assertIn("Search query: replacement", before_restore)
        self.assertEqual(
            before_restore.count("\x1b[H\x1b[2J" + active),
            2,
        )
        redraw = before_restore.rfind("\x1b[H\x1b[2J" + active)
        self.assertEqual(before_restore[redraw:], "\x1b[H\x1b[2J" + active)
        self.assertNotIn("replacement", before_restore[redraw:])
        script.assert_consumed(self)

    def test_prompt_escape_sequences_are_bounded_drained_and_never_leak(self) -> None:
        supported = (b"[A", b"[B", b"[5~", b"[6~")
        for sequence in supported:
            with self.subTest(kind="supported", sequence=sequence):
                data = b"/draft\x1b" + sequence + b"tail\x1bq"
                readiness = [True] * len(sequence) + [False]
                script = _RawScript(data, readiness)
                event, before_restore, _, _, _, _ = self._scripted_event(script)
                self.assertEqual(event, "close")
                self.assertIn("Search field: drafttail", before_restore)
                self.assertEqual(
                    before_restore.count("\x1b[H\x1b[2JPRIOR FRAME"),
                    2,
                )
                script.assert_consumed(self)

        unsupported = {
            "fragmented_csi": (b"[Z", [True, True]),
            "fragmented_ss3": (b"OP", [True, True]),
            "truncated": (b"[", [True, False]),
            "exact_32_byte_suffix": (b"[" + b"0" * 30 + b"Z", [True] * 32),
            "over_cap_33_byte_suffix": (b"[" + b"0" * 31 + b"Z", [True] * 33),
        }
        for name, (sequence, readiness) in unsupported.items():
            with self.subTest(kind=name, sequence_length=len(sequence)):
                script = _RawScript(b"/draft\x1b" + sequence + b"q", readiness)
                event, before_restore, _, _, _, _ = self._scripted_event(script)
                self.assertEqual(event, "close")
                self.assertEqual(
                    before_restore.count("\x1b[H\x1b[2JPRIOR FRAME"),
                    2,
                )
                self.assertNotIn("Search query: ", before_restore)
                script.assert_consumed(self)

    def test_prompt_eof_interrupt_and_cleanup_failure_are_deterministic(self) -> None:
        eof_cases: tuple[tuple[str, list[bytes | BaseException]], ...] = (
            ("field", [b"/", b"p", b""]),
            (
                "query",
                [
                    b"/",
                    b"c",
                    b"o",
                    b"n",
                    b"t",
                    b"e",
                    b"n",
                    b"t",
                    b"\n",
                    b"p",
                    b"",
                ],
            ),
        )
        for name, values in eof_cases:
            with self.subTest(name=name):
                script = _RawScript(values, [])
                event, before_restore, complete, host, _, _ = self._scripted_event(
                    script
                )
                self.assertIsNone(event)
                self.assertIn("\x1b[?25l", before_restore)
                self.assertTrue(complete.endswith("\x1b[?25h\x1b[?1049l"))
                self.assertIsNone(host._last_presented_frame)
                script.assert_consumed(self)

        interrupt = _RawScript(b"/\x03", [])
        interrupt_output = _TerminalBuffer()
        with self.assertRaises(KeyboardInterrupt):
            self._scripted_event(interrupt, output=interrupt_output)
        self.assertTrue(interrupt_output.getvalue().endswith("\x1b[?25h\x1b[?1049l"))
        interrupt.assert_consumed(self)

        primary = _RawScript([b"/", OSError("prompt read failed")], [])
        primary_output = _HideFailingBuffer()
        with self.assertRaisesRegex(OSError, "prompt read failed"):
            self._scripted_event(primary, output=primary_output)
        self.assertTrue(primary_output.getvalue().endswith("\x1b[?25h\x1b[?1049l"))
        primary.assert_consumed(self)

    def test_ordinary_line_escape_and_search_commands_remain_unchanged(self) -> None:
        input_stream = io.StringIO(
            "esc\n/content alpha\n/ content alpha\n/ content\nq\n"
        )
        host = _TerminalHost(input_stream, io.StringIO(), no_color=True)
        self.assertEqual(host.read_event(), "cancel")
        self.assertEqual(host.read_event(), "unknown")
        self.assertEqual(host.read_event(), "search\tcontent\talpha")
        self.assertEqual(host.read_event(), "unknown")
        self.assertEqual(host.read_event(), "close")

    def test_only_a_successfully_flushed_frame_is_retained(self) -> None:
        output = _FlushControlledBuffer()
        host = _TerminalHost(io.StringIO(), output, no_color=True)
        host.present("FRAME A")
        output.fail_next_flush = True
        with self.assertRaisesRegex(RuntimeError, "flush failed"):
            host.present("FRAME B")
        self.assertEqual(host._last_presented_frame, "FRAME A")
        host.close_view()
        self.assertIsNone(host._last_presented_frame)

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
                    "--searchable-field",
                    "content",
                ],
                input="/ content alpha\nesc\nesc\nesc\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Search content='alpha' • 1/2", completed.stdout)
            self.assertIn("Search cleared.", completed.stdout)
            self.assertNotIn("Unknown event", completed.stdout)
            self.assertEqual(path.read_bytes(), source)

    def test_raw_pty_field_escape_restores_exact_frame_and_next_command(self) -> None:
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
                    b"Search field: ",
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
                redraw = prompt_segment.rfind(_PRESENT)
                shown = prompt_segment.find(b"\x1b[?25h")
                hidden = prompt_segment.rfind(b"\x1b[?25l", 0, redraw)
                self.assertGreaterEqual(shown, 0)
                self.assertGreater(hidden, shown)
                self.assertGreater(redraw, hidden)

                self._pty_write(master, b"j")
                moved, moved_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Record 2/2",
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
                    b"Search field: ",
                    start=initial_end,
                )
                self._pty_write(master, b"content\r")
                query_prompt = self._pty_read_until(
                    master,
                    output,
                    b"Search query: ",
                    start=field_prompt,
                )
                self._pty_write(master, b"alpha\r")
                active, active_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search content='alpha'",
                    start=query_prompt,
                )
                self.assertIn(b"1/2", active)

                self._pty_write(master, b"/")
                second_field = self._pty_read_until(
                    master,
                    output,
                    b"Search field: ",
                    start=active_end,
                )
                self._pty_write(master, b"content\r")
                second_query = self._pty_read_until(
                    master,
                    output,
                    b"Search query: ",
                    start=second_field,
                )
                self._pty_write(master, b"replacement")
                self._pty_write(master, b"\x1b")
                restored, restored_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search content='alpha'",
                    start=second_query,
                )
                self.assertEqual(restored, active)

                self._pty_write(master, b"n")
                next_match, next_end = self._pty_frame_containing(
                    master,
                    output,
                    b"Search content='alpha'",
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
                self.assertNotIn(b"Search content=", ordinary)
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
                    "--searchable-field",
                    "content",
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
                "--searchable-field",
                "content",
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
        fake_termios.tcgetattr.return_value = ["saved"]
        fake_tty = mock.Mock()
        with (
            mock.patch.object(_standalone, "termios", fake_termios),
            mock.patch.object(_standalone, "tty", fake_tty),
            self.assertRaisesRegex(RuntimeError, "hosted failure"),
        ):
            with _TerminalHost(input_stream, output_stream, no_color=True):
                raise RuntimeError("hosted failure")

        fake_tty.setcbreak.assert_called_once_with(17)
        fake_termios.tcsetattr.assert_called_once_with(17, 1, ["saved"])
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
        self.assertEqual(len(modules), 8)
        self.assertEqual(sum(len(value) for value in graph.values()), 13)
        self.assertEqual(external, set())
        facade = (ROOT / "src/jsonl_viewer/__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("_standalone", facade)

        index = (ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        indexed = set(re.findall(r"^### `([^`]+)`$", index, flags=re.MULTILINE))
        self.assertEqual(indexed, set(modules))
        self.assertIn("Importable production units indexed: 8.", index)
        self.assertIn("Direct internal dependency edges indexed: 13.", index)
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
                "_model.py",
                "_render.py",
                "_standalone.py",
                "contracts.py",
                "engine.py",
                "py.typed",
            },
        )


if __name__ == "__main__":
    unittest.main()
