"""Standalone CLI and terminal owner outside embedded hosts."""

from __future__ import annotations

import argparse
import os
import select
import shutil
import sys
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
        self._last_presented_frame: str | None = None

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
        self._last_presented_frame = None
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
        self._last_presented_frame = frame

    def _prompt(self, label: str) -> str | None:
        if not self._interactive:
            self._output.write(label)
            self._output.flush()
            value = self._input.readline()
            return None if value == "" else value.rstrip("\r\n")
        assert termios is not None and tty is not None
        assert self._descriptor is not None and self._saved_attributes is not None
        termios.tcsetattr(
            self._descriptor,
            termios.TCSADRAIN,
            self._saved_attributes,
        )
        self._output.write("\n" + label)
        self._output.flush()
        try:
            value = self._input.readline()
        finally:
            tty.setcbreak(self._descriptor)
        return None if value == "" else value.rstrip("\r\n")

    def _escape_event(self) -> str:
        """Decode one main-view escape key or bounded terminal sequence."""

        assert self._descriptor is not None
        deadline = os.times().elapsed + 0.02
        suffix = bytearray()
        exceeded_recognition_bound = False
        while True:
            remaining = deadline - os.times().elapsed
            if (
                remaining <= 0
                or not select.select(
                    [self._descriptor],
                    [],
                    [],
                    remaining,
                )[0]
            ):
                return "cancel"
            current = os.read(self._descriptor, 1)
            if not current:
                return "cancel"
            if len(suffix) < 32:
                suffix.extend(current)
            else:
                exceeded_recognition_bound = True
            if len(suffix) == 1 and current in {b"[", b"O"}:
                continue
            if suffix[0] == ord("["):
                if 0x40 <= current[0] <= 0x7E:
                    break
                if not (0x20 <= current[0] <= 0x3F):
                    return "cancel"
                continue
            if suffix[0] == ord("O"):
                if 0x40 <= current[0] <= 0x7E:
                    break
                return "cancel"
            return "cancel"
        if exceeded_recognition_bound:
            return "cancel"
        return {
            b"[A": "up",
            b"[B": "down",
            b"[5~": "page_up",
            b"[6~": "page_down",
        }.get(bytes(suffix), "cancel")

    def _redraw_last_frame(self) -> None:
        """Restore the last complete engine frame after a local prompt cancel."""

        frame = self._last_presented_frame
        if frame is not None:
            self.present(frame)

    def _raw_prompt(self, label: str) -> tuple[str | None, bool]:
        """Collect one local raw-terminal line, distinguishing Escape from EOF."""

        assert self._descriptor is not None
        encoding = self._input.encoding or "utf-8"
        errors = self._input.errors or "strict"
        value = bytearray()
        self._output.write("\x1b[?25h\n" + label)
        self._output.flush()
        try:
            while True:
                current = os.read(self._descriptor, 1)
                if not current:
                    result: tuple[str | None, bool] = (None, False)
                    break
                if current == b"\x1b":
                    if self._escape_event() == "cancel":
                        result = (None, True)
                        break
                    continue
                if current in {b"\r", b"\n"}:
                    result = (bytes(value).decode(encoding, errors), False)
                    break
                if current == b"\x04":
                    result = (
                        None if not value else bytes(value).decode(encoding, errors),
                        False,
                    )
                    break
                if current == b"\x03":
                    raise KeyboardInterrupt
                if current in {b"\x08", b"\x7f"}:
                    while value:
                        value.pop()
                        try:
                            rendered = bytes(value).decode(encoding, errors)
                        except UnicodeDecodeError:
                            continue
                        self._output.write("\r\x1b[2K" + label + rendered)
                        self._output.flush()
                        break
                    continue
                if current < b"\x20":
                    continue
                value.extend(current)
                try:
                    rendered = bytes(value).decode(encoding, errors)
                except UnicodeDecodeError as exc:
                    if exc.end == len(value) and exc.reason == "unexpected end of data":
                        continue
                    raise
                self._output.write("\r\x1b[2K" + label + rendered)
                self._output.flush()
        except BaseException:
            try:
                self._output.write("\x1b[?25l")
                self._output.flush()
            except BaseException:
                pass
            raise
        self._output.write("\x1b[?25l")
        self._output.flush()
        return result

    def _raw_event(self) -> str | None:
        assert self._descriptor is not None
        while True:
            value = os.read(self._descriptor, 1)
            if not value:
                return None
            if value == b"\x1b":
                return self._escape_event()
            mapping = {
                b"q": "close",
                b"j": "down",
                b"k": "up",
                b" ": "page_down",
                b"b": "page_up",
                b"n": "next_match",
                b"N": "previous_match",
                b"m": "toggle_mode",
                b"h": "help",
                b"?": "help",
            }
            if value in mapping:
                return mapping[value]
            if value == b"g":
                line = self._prompt("Go to source line: ")
                return None if line is None else "goto\t" + line
            if value == b"/":
                field, cancelled = self._raw_prompt("Search field: ")
                if cancelled:
                    self._redraw_last_frame()
                    continue
                if field is None:
                    return None
                query, cancelled = self._raw_prompt("Search query: ")
                if cancelled:
                    self._redraw_last_frame()
                    continue
                return None if query is None else f"search\t{field}\t{query}"
            return "unknown"

    def _line_event(self) -> str | None:
        self._output.write("viewer> ")
        self._output.flush()
        value = self._input.readline()
        if value == "":
            return None
        command = value.strip()
        mapping = {
            "q": "close",
            "quit": "close",
            "j": "down",
            "down": "down",
            "k": "up",
            "up": "up",
            "pgdn": "page_down",
            "pgup": "page_up",
            "n": "next_match",
            "N": "previous_match",
            "m": "toggle_mode",
            "h": "help",
            "?": "help",
            "esc": "cancel",
        }
        if command in mapping:
            return mapping[command]
        if command.startswith("g "):
            return "goto\t" + command[2:].strip()
        if command.startswith("/ "):
            parts = command[2:].split(maxsplit=1)
            if len(parts) == 2:
                return f"search\t{parts[0]}\t{parts[1]}"
        return "unknown"

    def read_event(self) -> str | None:
        return self._raw_event() if self._interactive else self._line_event()

    def close_view(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._last_presented_frame = None
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
    parser.add_argument(
        "--searchable-field",
        action="append",
        dest="searchable_fields",
        default=[],
        metavar="FIELD",
        help="allow exact top-level field search; repeatable",
    )
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
            searchable_fields=tuple(arguments.searchable_fields),
            date_time_field=arguments.date_time_field,
            request_type_field=arguments.request_type_field,
            content_field=arguments.content_field,
            title=arguments.title,
            conversation_label=arguments.conversation_label,
            conversation_subject=arguments.conversation_subject,
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
