"""Deterministic injected host used by viewer contract tests."""

from __future__ import annotations

from collections.abc import Iterable
import re


def without_caret(frame: str) -> str:
    """Remove only the extra plain-mode character-cursor row."""
    return "\n".join(line for line in frame.splitlines() if line.strip() != "^")


def styled_text(frame: str, code: str) -> str:
    """Collect readable text carrying one SGR attribute, across cell boundaries."""
    return "".join(text for style, text in re.findall(
        r"\x1b\[([0-9;]*)m([^\x1b]*)\x1b\[0m", frame
    ) if code in style.split(";"))


class FakeHost:
    def __init__(
        self,
        events: Iterable[str | None] = ("close",),
        *,
        size: tuple[int, int] = (100, 24),
        color: bool = False,
    ) -> None:
        self._events = iter(events)
        self._size = size
        self._color = color
        self.frames: list[str] = []
        self.close_calls = 0

    def terminal_size(self) -> tuple[int, int]:
        return self._size

    def color_enabled(self) -> bool:
        return self._color

    def present(self, frame: str) -> None:
        self.frames.append(frame)

    def read_event(self) -> str | None:
        return next(self._events, None)

    def close_view(self) -> None:
        self.close_calls += 1
