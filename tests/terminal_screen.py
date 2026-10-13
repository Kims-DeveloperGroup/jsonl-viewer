"""Small independent terminal model for owned-screen restoration tests.

Only the controls used by the viewer are accepted.  The optional ED2 behavior
models a terminal that archives erased nonempty rows, rather than a GUI brand.
"""

from __future__ import annotations

import re
import unicodedata


_CSI = re.compile(r"\x1b\[([0-9;?]*)([@-~])")


class TerminalScreen:
    def __init__(self, columns: int, rows: int, *, archive_ed2: bool,
                 eager_wrap: bool = False) -> None:
        self.columns, self.rows = columns, rows
        self.archive_ed2 = archive_ed2
        self.eager_wrap = eager_wrap
        self.primary = self._blank()
        self.alternate = self._blank()
        self.history = ["prior shell history"]
        self.in_alternate = False
        self.row = self.column = 0
        self.wrap_pending = False
        self.saved_cursor = (0, 0, False)
        self.alternate_scrolls = 0
        self.erase_display_count = 0
        self._last_cell: tuple[int, int] | None = None

    def _blank(self) -> list[list[str | None]]:
        return [[" "] * self.columns for _ in range(self.rows)]

    @property
    def buffer(self) -> list[list[str | None]]:
        return self.alternate if self.in_alternate else self.primary

    @staticmethod
    def _line(cells: list[str | None]) -> str:
        return "".join(cell or "" for cell in cells).rstrip()

    def lines(self) -> tuple[str, ...]:
        return tuple(self._line(row) for row in self.buffer)

    def primary_state(self) -> tuple[tuple[str, ...], tuple[str, ...], int, int, bool]:
        return (tuple(self._line(row) for row in self.primary), tuple(self.history),
                self.row, self.column, self.in_alternate)

    def resize(self, columns: int, rows: int) -> None:
        for buffer in (self.primary, self.alternate):
            for row in buffer:
                row[:] = (row + [" "] * columns)[:columns]
            buffer[:] = (buffer + [[" "] * columns for _ in range(rows)])[:rows]
        self.columns, self.rows = columns, rows
        self.row = min(self.row, rows - 1)
        self.column = min(self.column, columns - 1)
        self.wrap_pending = False
        self._last_cell = None

    def _linefeed(self) -> None:
        self.wrap_pending = False
        if self.row < self.rows - 1:
            self.row += 1
            return
        erased = self.buffer.pop(0)
        self.buffer.append([" "] * self.columns)
        if self._last_cell is not None:
            row, column = self._last_cell
            self._last_cell = (row - 1, column) if row else None
        if self.in_alternate:
            self.alternate_scrolls += 1
        if not self.in_alternate or self.archive_ed2:
            self.history.append(self._line(erased))

    def _character(self, character: str) -> None:
        if unicodedata.combining(character):
            if self._last_cell is not None:
                row, column = self._last_cell
                self.buffer[row][column] += character  # type: ignore[operator]
            return
        width = 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
        if width > self.columns:
            return
        if self.wrap_pending or self.column + width > self.columns:
            self.column = 0
            self._linefeed()
        self.buffer[self.row][self.column] = character
        self._last_cell = (self.row, self.column)
        if width == 2:
            self.buffer[self.row][self.column + 1] = None
        next_column = self.column + width
        self.wrap_pending = next_column == self.columns
        self.column = min(next_column, self.columns - 1)
        if self.wrap_pending and self.eager_wrap:
            self.column = 0
            self._linefeed()

    def _control(self, parameters: str, final: str) -> None:
        if final == "m":
            return
        if parameters.startswith("?") and final in ("h", "l"):
            for number in parameters[1:].split(";"):
                if number == "1049":
                    if final == "h":
                        self.saved_cursor = (self.row, self.column, self.wrap_pending)
                        self.in_alternate = True
                        self.alternate = self._blank()
                        self.row = self.column = 0
                        self.wrap_pending = False
                    else:
                        self.in_alternate = False
                        self.row, self.column, self.wrap_pending = self.saved_cursor
                elif number not in ("25", "1000", "1006"):
                    raise AssertionError(f"unsupported terminal mode: {number}")
            return
        if final in ("H", "f"):
            values = parameters.split(";") if parameters else []
            row = int(values[0] or "1") if values else 1
            column = int(values[1] or "1") if len(values) > 1 else 1
            self.row = min(max(row - 1, 0), self.rows - 1)
            self.column = min(max(column - 1, 0), self.columns - 1)
            self.wrap_pending = False
            self._last_cell = None
            return
        if final == "K" and parameters == "2":
            self.buffer[self.row] = [" "] * self.columns
            self.wrap_pending = False
            return
        if final == "J" and parameters == "2":
            self.erase_display_count += 1
            if self.archive_ed2:
                self.history.extend(line for row in self.buffer if (line := self._line(row)))
            self.buffer[:] = self._blank()
            self.wrap_pending = False
            return
        raise AssertionError(f"unsupported terminal control: {parameters}{final}")

    def feed(self, text: str) -> None:
        position = 0
        while position < len(text):
            character = text[position]
            if character == "\x1b":
                match = _CSI.match(text, position)
                if match is None:
                    raise AssertionError(f"unsupported terminal escape: {text[position:position + 16]!r}")
                self._control(*match.groups())
                position = match.end()
                continue
            if character == "\n":
                self._linefeed()
                self._last_cell = None
            elif character == "\r":
                self.column = 0
                self.wrap_pending = False
                self._last_cell = None
            elif ord(character) < 32 or character == "\x7f":
                raise AssertionError(f"unsupported terminal character: {character!r}")
            else:
                self._character(character)
            position += 1
