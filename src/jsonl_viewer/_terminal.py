"""One reusable owner for terminal resources and physical input transport."""

from __future__ import annotations

import codecs
from collections import deque
from copy import deepcopy
from dataclasses import replace
import os
import select
import signal
import sys
import threading
import time
from types import TracebackType
from typing import Any, Callable

from ._mouse import decode_sgr
from .contracts import ViewerSpec, ViewerTerminal
from .engine import view_jsonl

try:  # pragma: no cover - platform-specific ordinary-line fallback.
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_ENTER = "\x1b[?1049h\x1b[?25l\x1b[?1006h\x1b[?1000h"
_LEAVE = "\x1b[?1000l\x1b[?1006l\x1b[H\x1b[2J\x1b[?25h\x1b[?1049l"
_INVALID_MOUSE = "mouse\tinvalid"


class _ReadTimeout(Exception):
    """A read deadline elapsed without finalizing a buffered UTF-8 decoder."""


class _TerminalTermination(BaseException):
    """Carry one original default termination through terminal restoration."""

    def __init__(self, number: int) -> None:
        self.number = number
        super().__init__(number)


class _TerminalHost:
    """Acquire resources for one view; never close its supplied streams."""

    def __init__(self, terminal: ViewerTerminal, *, transient_only: bool = False) -> None:
        self._input = terminal.input_stream
        self._output = terminal.output_stream
        self._reader = terminal.read_character
        self._no_color = terminal.no_color
        self._transient_only = transient_only
        self._descriptor: int | None = None
        self._output_descriptor: int | None = None
        self._saved_attributes: list[Any] | None = None
        self._saved_signals: dict[int, Any] = {}
        self._signal_handler = self._handle_termination
        self._termination: _TerminalTermination | None = None
        self._attributes_changed = False
        self._restoration_failed = False
        self._interactive = False
        self._terminal_started = False
        self._closed = False
        self._restoring = False
        self._decoder = codecs.getincrementaldecoder(self._input.encoding or "utf-8")(
            errors=self._input.errors or "strict"
        )
        self._pending: deque[str] = deque()
        self._pending_csi: tuple[str, int] | None = None
        self._discard_mouse = False
        self._mouse_received = 0

    def __enter__(self) -> _TerminalHost:
        supported = (termios is not None and tty is not None
                     and self._input.isatty() and self._output.isatty()
                     and os.environ.get("TERM", "") != "dumb")
        if not supported:
            if self._transient_only:
                raise RuntimeError("transient viewer requires an interactive terminal")
            return self
        assert termios is not None and tty is not None
        try:
            self._descriptor = self._input.fileno()
            self._output_descriptor = self._output.fileno()
            self._saved_attributes = deepcopy(termios.tcgetattr(self._descriptor))
        except (OSError, ValueError):
            if self._transient_only:
                raise RuntimeError("transient viewer requires an interactive terminal") from None
            return self
        try:
            self._install_signal_handlers()
            # A partially completed tty mutation also requires restoration.
            self._attributes_changed = True
            tty.setcbreak(self._descriptor, termios.TCSANOW)
            active = termios.tcgetattr(self._descriptor)
            active[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(self._descriptor, termios.TCSANOW, active)
            self._interactive = True
            # Mark acquisition before the write: partial writes require cleanup.
            self._terminal_started = True
            self._output.write(_ENTER)
            self._output.flush()
        except BaseException as failure:
            self._restore_terminal(failure)
            self._redeliver_termination()
            raise
        return self

    def __exit__(self, _exception_type: type[BaseException] | None,
                 exception: BaseException | None, _traceback: TracebackType | None) -> None:
        self._restore_terminal(exception)
        self._redeliver_termination()

    def _handle_termination(self, number: int, _frame: object) -> None:
        if self._termination is not None:
            return
        self._termination = _TerminalTermination(number)
        # Cleanup must finish even if the first signal arrives during it.
        if not self._restoring:
            raise self._termination

    def _redeliver_termination(self) -> None:
        if self._termination is not None and not self._restoration_failed:
            termination, self._termination = self._termination, None
            # Dispositions and terminal resources are restored before delivery.
            signal.raise_signal(termination.number)
            # Default POSIX delivery cannot return; preserve intent if mocked.
            raise termination

    def _install_signal_handlers(self) -> None:
        defaults = tuple(number for name in ("SIGTERM", "SIGHUP")
                         if (number := getattr(signal, name, None)) is not None
                         and signal.getsignal(number) == signal.SIG_DFL)
        if defaults and threading.current_thread() is not threading.main_thread():
            raise RuntimeError("viewer-owned default signal cleanup requires main thread")
        for number in defaults:
            # Record before installation: signal may arrive as signal() returns.
            self._saved_signals[number] = signal.SIG_DFL
            signal.signal(number, self._signal_handler)

    def _restore_terminal(self, active_failure: BaseException | None = None) -> None:
        if self._restoring:
            return
        self._restoring = True
        failures: list[BaseException] = []
        try:
            if self._terminal_started:
                self._terminal_started = False
                try:
                    self._output.write(_LEAVE)
                    self._output.flush()
                except BaseException as failure:
                    failures.append(failure)
            if (self._attributes_changed and self._saved_attributes is not None
                    and self._descriptor is not None):
                saved, self._saved_attributes = self._saved_attributes, None
                self._attributes_changed = False
                try:
                    assert termios is not None
                    termios.tcsetattr(self._descriptor, termios.TCSADRAIN, saved)
                except BaseException as failure:
                    failures.append(failure)
            for number, handler in reversed(tuple(self._saved_signals.items())):
                try:
                    current = signal.getsignal(number)
                    if current == self._signal_handler:
                        signal.signal(number, handler)
                    elif current != handler:
                        raise RuntimeError("viewer-owned signal disposition changed")
                except BaseException as failure:
                    failures.append(failure)
            self._saved_signals.clear()
            self._interactive = False
        finally:
            self._restoring = False
        if failures:
            self._restoration_failed = True
            if active_failure is None:
                raise failures[0]

    def terminal_size(self) -> tuple[int, int]:
        dimensions: list[int] = []
        for name in ("COLUMNS", "LINES"):
            try:
                value = int(os.environ.get(name, ""))
            except ValueError:
                value = 0
            dimensions.append(value if value > 0 else 0)
        if not all(dimensions):
            try:
                descriptor = self._output_descriptor
                if descriptor is None:
                    descriptor = self._output.fileno()
                size = os.get_terminal_size(descriptor)
                actual = (size.columns, size.lines)
            except (AttributeError, OSError, ValueError):
                actual = (100, 30)
            dimensions = [override or observed for override, observed in zip(dimensions, actual)]
        return dimensions[0], dimensions[1]

    def color_enabled(self) -> bool:
        return (not self._no_color and "NO_COLOR" not in os.environ
                and self._output.isatty() and os.environ.get("TERM", "") != "dumb")

    def present(self, frame: str) -> None:
        self._output.write("\x1b[H\x1b[2J" + frame if self._interactive else frame + "\n")
        self._output.flush()

    def _before_read(self, deadline: float) -> Callable[[], None]:
        def wait() -> None:
            assert self._descriptor is not None
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._descriptor], [], [], remaining)[0]:
                raise _ReadTimeout
        return wait

    def _character(self, deadline: float) -> str:
        if self._pending:
            return self._pending.popleft()
        before_read = self._before_read(deadline)
        if self._reader is not None:
            # A caller's reader checks readiness only after its buffered input.
            character = self._reader(before_read)
            if type(character) is not str or len(character) > 1:
                raise ValueError("read_character must return one decoded character")
            if not character:
                raise EOFError
            return character
        assert self._descriptor is not None
        while True:
            before_read()
            raw = os.read(self._descriptor, 1)
            if not raw:
                self._decoder.decode(b"", final=True)
                raise EOFError
            character = self._decoder.decode(raw)
            if character:
                return character

    def _mouse_event(self, deadline: float, report: str = "") -> str:
        discarded = self._discard_mouse
        self._discard_mouse = False
        while True:
            try:
                current = self._character(deadline)
            except _ReadTimeout:
                self._discard_mouse = True
                return _INVALID_MOUSE
            except EOFError:
                return _INVALID_MOUSE
            self._mouse_received += 1
            if self._mouse_received > 4_096:
                raise ValueError("terminal escape sequence exceeds its drain bound")
            if current in {"M", "m"}:
                self._mouse_received = 0
                return _INVALID_MOUSE if discarded else decode_sgr(report + current)
            if current not in "0123456789;":
                # A malformed/partial report must not eat the next keyboard key.
                self._pending.appendleft(current)
                self._mouse_received = 0
                return _INVALID_MOUSE
            if len(report) < 21:
                report += current

    def _escape_event(self, prefix: tuple[str, int] | None = None) -> str:
        deadline = time.monotonic() + 0.02
        suffix, received = prefix if prefix is not None else ("", 0)
        resumed = prefix is not None
        while True:
            try:
                current = self._character(deadline)
            except _ReadTimeout:
                if suffix.startswith("["):
                    # CSI may be a mouse report whose '<' has not arrived yet.
                    # Keep the bounded prefix; it must not cancel prompt drafts.
                    self._pending_csi = (suffix, received)
                    return _INVALID_MOUSE
                return "key\tescape" if not received else "key\tunknown_escape"
            except EOFError:
                if suffix.startswith("["):
                    return _INVALID_MOUSE
                return "key\tescape" if not received else "key\tunknown_escape"
            received += 1
            if received > 4_096:
                raise ValueError("terminal escape sequence exceeds its drain bound")
            if len(suffix) < 32:
                suffix += current
            if suffix == "[<":
                self._mouse_received = 0
                return self._mouse_event(deadline)
            if received == 1 and current in {"[", "O"}:
                continue
            if suffix[0] == "[":
                if "@" <= current <= "~":
                    break
                if not " " <= current <= "?":
                    if resumed:
                        self._pending.appendleft(current)
                        return _INVALID_MOUSE
                    return "key\tunknown_escape"
                continue
            if suffix[0] == "O" and "@" <= current <= "~":
                break
            return "key\tunknown_escape"
        if received > 32:
            return _INVALID_MOUSE if resumed else "key\tunknown_escape"
        key = {
            "[A": "up", "OA": "up", "[B": "down", "OB": "down",
            "[C": "right", "OC": "right", "[D": "left", "OD": "left",
            "[H": "home", "OH": "home", "[1~": "home", "[7~": "home",
            "[F": "end", "OF": "end", "[4~": "end", "[8~": "end",
            "[3~": "delete", "[5~": "page_up", "[6~": "page_down",
        }.get(suffix)
        if key is None and resumed:
            # Mouse terminators remain inert even for malformed CSI reports.
            # Other new ordinary keys after a timed-out prefix are retained.
            if current not in {"M", "m"}:
                self._pending.appendleft(current)
            return _INVALID_MOUSE
        return "key\t" + (key or "unknown_escape")

    def _configured_control(self, character: str) -> str | None:
        if self._saved_attributes is None or termios is None:
            return None
        for index, key in ((termios.VINTR, "interrupt"), (termios.VEOF, "eof")):
            value = self._saved_attributes[6][index]
            raw = bytes((value,)) if isinstance(value, int) else value
            if raw != b"\x00" and character.encode("utf-8") == raw:
                return "key\t" + key
        return None

    def _raw_event(self) -> str | None:
        deadline = time.monotonic() + 0.5
        if self._pending_csi is not None:
            prefix, self._pending_csi = self._pending_csi, None
            return self._escape_event(prefix)
        if self._discard_mouse:
            return self._mouse_event(deadline)
        try:
            character = self._character(deadline)
        except _ReadTimeout:
            return "idle"
        except EOFError:
            return None
        configured = self._configured_control(character)
        if configured is not None:
            return configured
        if character == "\x1b":
            return self._escape_event()
        controls = {
            "\r": "enter", "\n": "enter", "\x08": "backspace", "\x7f": "backspace",
            "\x01": "ctrl_a", "\x05": "ctrl_e", "\x15": "ctrl_u", "\x0b": "ctrl_k", "\x17": "ctrl_w",
        }
        if character in controls:
            return "key\t" + controls[character]
        if character < " " or character == "\x7f":
            return "key\tunknown"
        return "text\t" + character

    def _line_event(self) -> str | None:
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
        self._restore_terminal()


def view_owned(source: bytes, spec: ViewerSpec, terminal: ViewerTerminal | None,
               *, transient_only: bool) -> None:
    if terminal is None:
        terminal = ViewerTerminal(sys.stdin, sys.stdout)
    with _TerminalHost(terminal, transient_only=transient_only) as host:
        view_jsonl(source, replace(spec, input_protocol="keys"), host)
