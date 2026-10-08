"""Pure sparse display-cell indexes for already formatted Unicode text."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable
import unicodedata


CHECKPOINT_STRIDE = 256


def character_cells(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def text_cells(text: str) -> int:
    return len(text) if text.isascii() else sum(character_cells(c) for c in text)


def _spans(text: str, offset: int = 0, column: int = 0) -> Iterable[tuple[int, int, int, int]]:
    """Yield cell column, code-point start/end and width for intact clusters."""
    while offset < len(text):
        start = offset
        width = character_cells(text[offset])
        offset += 1
        while offset < len(text) and character_cells(text[offset]) == 0:
            offset += 1
        yield column, start, offset, width
        column += width


def clusters(text: str) -> Iterable[str]:
    if text.isascii():
        yield from text
    else:
        for _, start, end, _ in _spans(text):
            yield text[start:end]


@dataclass(frozen=True, slots=True)
class TextLayout:
    text: str
    cells: int
    columns: tuple[int, ...]
    offsets: tuple[int, ...]
    ascii: bool

    @classmethod
    def build(cls, text: str) -> TextLayout:
        if text.isascii():
            return cls(text, len(text), (0,), (0,), True)
        columns, offsets = [0], [0]
        threshold = CHECKPOINT_STRIDE
        cells = 0
        for column, start, _, width in _spans(text):
            if column >= threshold:
                columns.append(column)
                offsets.append(start)
                threshold = (column // CHECKPOINT_STRIDE + 1) * CHECKPOINT_STRIDE
            cells = column + width
        return cls(text, cells, tuple(columns), tuple(offsets), False)

    def window(self, start: int, end: int) -> Iterable[tuple[int, int, int, int]]:
        if end <= 0 or start >= self.cells or start >= end:
            return
        if self.ascii:
            for offset in range(max(0, start), min(self.cells, end)):
                yield offset, offset, offset + 1, 1
            return
        checkpoint = max(0, bisect_right(self.columns, max(0, start)) - 1)
        for column, begin, finish, width in _spans(
                self.text, self.offsets[checkpoint], self.columns[checkpoint]):
            if column >= end:
                break
            if width and column >= start and column + width <= end:
                yield column, begin, finish, width

    def nearest(self, column: int) -> tuple[int, int, int, int] | None:
        if not self.cells:
            return None
        column = min(max(0, column), self.cells - 1)
        return min(self.window(max(0, column - 2), column + 4),
                   key=lambda span: (abs(span[0] - column), span[0]), default=None)


@dataclass(frozen=True, slots=True)
class RowLayout:
    parts: tuple[TextLayout, ...]
    starts: tuple[int, ...]
    cells: int
    checkpoints: int

    @classmethod
    def build(cls, texts: Iterable[str]) -> RowLayout:
        parts = tuple(TextLayout.build(text) for text in texts)
        starts = []
        cells = 0
        for part in parts:
            starts.append(cells)
            cells += part.cells
        return cls(parts, tuple(starts), cells, sum(len(part.columns) for part in parts))

    def window(self, start: int, end: int) -> Iterable[tuple[int, int, int, int, int]]:
        """Yield segment index plus global cell and local code-point bounds."""
        if not self.parts or end <= 0 or start >= self.cells:
            return
        first = max(0, bisect_right(self.starts, max(0, start)) - 1)
        for index in range(first, len(self.parts)):
            column = self.starts[index]
            if column >= end:
                break
            for cell, begin, finish, width in self.parts[index].window(start - column, end - column):
                yield index, column + cell, begin, finish, width
