"""Deterministic injected host used by viewer contract tests."""

from __future__ import annotations

from collections.abc import Iterable


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
