"""Bounded physical mouse decoding and rendered-cell hit testing."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ._model import RenderResult, VisibleCharacter

_REPORT = re.compile(r"([0-9]{1,3});([0-9]{1,6});([0-9]{1,6})([Mm])\Z")
_ENVELOPE = re.compile(r"mouse\t([0-9]{1,3})\t([0-9]{1,6})\t([0-9]{1,6})\t(press|release)\Z")


@dataclass(frozen=True, slots=True)
class MouseEvent:
    button: int
    column: int
    row: int
    phase: str

    @property
    def action(self) -> str | None:
        if self.phase != "press":
            return None
        # SGR modifier bits are orthogonal to button and motion identity.
        code = self.button & ~(4 | 8 | 16)
        return {0: "click", 64: "cursor_up", 65: "cursor_down"}.get(code)


def parse_mouse(event: str) -> MouseEvent | None:
    if len(event) > 32:
        return None
    match = _ENVELOPE.fullmatch(event)
    if match is None:
        return None
    button, column, row = map(int, match.groups()[:3])
    if button > 255 or column < 1 or row < 1:
        return None
    return MouseEvent(button, column, row, match[4])


def decode_sgr(report: str) -> str:
    """Normalize the bounded body following CSI '<'; invalid reports are inert."""

    if len(report) > 20:
        return "mouse\tinvalid"
    match = _REPORT.fullmatch(report)
    if match is None:
        return "mouse\tinvalid"
    event = "mouse\t" + "\t".join(match.groups()[:3]) + "\t" + (
        "press" if match[4] == "M" else "release"
    )
    return event if parse_mouse(event) is not None else "mouse\tinvalid"


def hit_test(event: MouseEvent, frame: RenderResult) -> VisibleCharacter | None:
    if not (1 <= event.column <= frame.columns and 1 <= event.row <= frame.rows):
        return None
    column, row = event.column - 1, event.row - 1
    return next((cell for cell in frame.characters
                 if cell.screen_row == row
                 and cell.screen_column <= column < cell.screen_column + cell.screen_width), None)
