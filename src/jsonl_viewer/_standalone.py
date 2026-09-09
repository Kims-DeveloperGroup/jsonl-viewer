"""Standalone CLI and terminal owner outside embedded hosts."""

from __future__ import annotations

import argparse
import codecs
import os
import select
import shutil
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, TextIO

from ._input import MAX_SOURCE_BYTES
from .contracts import ViewerSpec
from .engine import view_jsonl

try:  # pragma: no cover - platform availability is exercised by import tests.
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows ordinary-line fallback.
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


class _TerminalHost:
    """Own one standalone terminal lifecycle around the generic viewer."""

    def __init__(
        self,
        input_stream: TextIO,
        output_stream: TextIO,
        *,
        no_color: bool,
    ) -> None:
        self._input = input_stream
        self._output = output_stream
        self._no_color = no_color
        self._descriptor: int | None = None
        # The platform-specific termios list has no portable precise shape.
        self._saved_attributes: list[Any] | None = None
        self._interactive = False
        self._closed = False
        self._decoder = codecs.getincrementaldecoder(input_stream.encoding or "utf-8")(
            errors=input_stream.errors or "strict"
        )

    def __enter__(self) -> _TerminalHost:
        if (
            termios is not None
            and tty is not None
            and self._input.isatty()
            and self._output.isatty()
        ):
            descriptor = self._input.fileno()
            self._descriptor = descriptor
            self._saved_attributes = termios.tcgetattr(descriptor)
            self._interactive = True
            try:
                tty.setcbreak(descriptor)
                self._output.write("\x1b[?1049h\x1b[?25l")
                self._output.flush()
            except BaseException:
                self._restore_terminal()
                raise
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._restore_terminal()

    def _restore_terminal(self) -> None:
        if self._interactive and self._descriptor is not None:
            assert termios is not None
            assert self._saved_attributes is not None
            try:
                termios.tcsetattr(
                    self._descriptor,
                    termios.TCSADRAIN,
                    self._saved_attributes,
                )
            finally:
                try:
                    self._output.write("\x1b[?25h\x1b[?1049l")
                    self._output.flush()
                finally:
                    self._interactive = False

    def terminal_size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size(fallback=(100, 30))
        return size.columns, size.lines

    def color_enabled(self) -> bool:
        return (
            not self._no_color
            and "NO_COLOR" not in os.environ
            and self._output.isatty()
            and os.environ.get("TERM", "") != "dumb"
        )

    def present(self, frame: str) -> None:
        if self._interactive:
            self._output.write("\x1b[H\x1b[2J" + frame)
        else:
            self._output.write(frame + "\n")
        self._output.flush()

    def _escape_event(self) -> str:
        """Decode a bounded physical CSI/SS3 sequence without viewer bindings."""

        assert self._descriptor is not None
        deadline = time.monotonic() + 0.02
        suffix = bytearray()
        received = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select(
                [self._descriptor], [], [], remaining
            )[0]:
                return "key\tescape" if not received else "key\tunknown_escape"
            current = os.read(self._descriptor, 1)
            if not current:
                return "key\tescape" if not received else "key\tunknown_escape"
            received += 1
            if received > 4_096:
                raise ValueError("terminal escape sequence exceeds its drain bound")
            if len(suffix) < 32:
                suffix.extend(current)
            if received == 1 and current in {b"[", b"O"}:
                continue
            if suffix[0] == ord("["):
                if 0x40 <= current[0] <= 0x7E:
                    break
                if not 0x20 <= current[0] <= 0x3F:
                    return "key\tunknown_escape"
                continue
            if suffix[0] == ord("O") and 0x40 <= current[0] <= 0x7E:
                break
            return "key\tunknown_escape"
        if received > 32:
            return "key\tunknown_escape"
        key = {
            b"[A": "up", b"OA": "up", b"[B": "down", b"OB": "down",
            b"[C": "right", b"OC": "right", b"[D": "left", b"OD": "left",
            b"[H": "home", b"OH": "home", b"[1~": "home", b"[7~": "home",
            b"[F": "end", b"OF": "end", b"[4~": "end", b"[8~": "end",
            b"[3~": "delete", b"[5~": "page_up", b"[6~": "page_down",
        }.get(bytes(suffix), "unknown_escape")
        return "key\t" + key

    def _configured_control(self, raw_character: bytes) -> str | None:
        if self._saved_attributes is None or termios is None:
            return None
        for index, key in ((termios.VINTR, "interrupt"), (termios.VEOF, "eof")):
            value = self._saved_attributes[6][index]
            raw = bytes((value,)) if isinstance(value, int) else value
            if raw != b"\x00" and raw_character == raw:
                return "key\t" + key
        return None

    def _raw_event(self) -> str | None:
        assert self._descriptor is not None
        while True:
            value = os.read(self._descriptor, 1)
            if not value:
                self._decoder.decode(b"", final=True)
                return None
            configured = self._configured_control(value)
            if configured is not None:
                return configured
            character = self._decoder.decode(value)
            if not character:
                continue
            if character == "\x1b":
                return self._escape_event()
            controls = {
                "\r": "enter", "\n": "enter", "\x08": "backspace",
                "\x7f": "backspace", "\x01": "ctrl_a", "\x05": "ctrl_e",
                "\x15": "ctrl_u", "\x0b": "ctrl_k", "\x17": "ctrl_w",
            }
            if character in controls:
                return "key\t" + controls[character]
            if character < " " or character == "\x7f":
                return "key\tunknown"
            return "text\t" + character

    def _line_event(self) -> str | None:
        # The host frames bounded literal transport, never command grammar.
        value = self._input.readline(8_195)
        if value == "":
            return None
        line = value.removesuffix("\n").removesuffix("\r")
        if len(line) > 8_192:
            consumed = len(value)
            while value and not value.endswith("\n"):
                value = self._input.readline(min(8_195, 65_536 - consumed + 1))
                consumed += len(value)
                if consumed > 65_536:
                    raise ValueError("terminal line exceeds its drain bound")
            return "key\tunknown"
        return "line\t" + line

    def read_event(self) -> str | None:
        try:
            return self._raw_event() if self._interactive else self._line_event()
        except KeyboardInterrupt:
            return "key\tinterrupt"

    def close_view(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._decoder.reset()
        if self._interactive:
            self._output.write("\x1b[H\x1b[2J")
            self._output.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsonl-viewer",
        description="View one immutable strict UTF-8 JSONL snapshot.",
    )
    parser.add_argument("path", help="JSONL path, or - to snapshot standard input")
    parser.add_argument("--session", required=True, help="header session value")
    parser.add_argument(
        "--conversation",
        required=True,
        help="header conversation/scope ID",
    )
    parser.add_argument(
        "--conversation-label",
        default="Conversation",
        help="header scope label, such as Conversation or Debate",
    )
    parser.add_argument(
        "--conversation-subject",
        help="optional header scope subject",
    )
    parser.add_argument("--agent", required=True, help="header agent value")
    parser.add_argument("--date-time-field", default="timestamp")
    parser.add_argument("--request-type-field", default="request_type")
    parser.add_argument("--content-field", default="content")
    parser.add_argument("--title", default="JSONL Viewer")
    parser.add_argument("--no-color", action="store_true")
    return parser


def _read_bounded(handle: BinaryIO) -> bytes:
    return handle.read(MAX_SOURCE_BYTES + 1)


def main(argv: list[str] | None = None) -> int:
    """Run the standalone terminal owner without changing the source file."""

    arguments = _parser().parse_args(argv)
    owned_input: TextIO | None = None
    try:
        if arguments.path == "-":
            source = _read_bounded(sys.stdin.buffer)
            owned_input = open(os.devnull, encoding="utf-8")
            input_stream: TextIO = owned_input
        else:
            path = Path(arguments.path)
            with path.open("rb") as handle:
                source = _read_bounded(handle)
            input_stream = sys.stdin
        spec = ViewerSpec(
            session_id=arguments.session,
            conversation_id=arguments.conversation,
            agent_id=arguments.agent,
            date_time_field=arguments.date_time_field,
            request_type_field=arguments.request_type_field,
            content_field=arguments.content_field,
            title=arguments.title,
            conversation_label=arguments.conversation_label,
            conversation_subject=arguments.conversation_subject,
            input_protocol="keys",
        )
    except (OSError, TypeError, ValueError) as exc:
        if owned_input is not None:
            owned_input.close()
        print(f"jsonl-viewer: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        with _TerminalHost(
            input_stream,
            sys.stdout,
            no_color=arguments.no_color,
        ) as host:
            view_jsonl(source, spec, host)
    finally:
        if owned_input is not None:
            owned_input.close()
    return 0
