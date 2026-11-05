"""Independent audit/log data and deterministic hosts for generic inspection."""

from __future__ import annotations

import json
from dataclasses import dataclass

from jsonl_viewer._render import _text_cells, strip_ansi
from tests.support import FakeHost


def jsonl(*records: object) -> bytes:
    """Preserve input roots and Unicode without adding application metadata."""
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()


def audit_records() -> tuple[object, ...]:
    """Five noncontiguous alpha events, one beta event, and two orphans."""
    return (
        {"trace": "alpha", "event": "open", "level": "info", "message": "연결 시작"},
        {"schema": "customer_account", "message": "standalone one"},
        {"trace": "alpha", "event": "read", "level": "warn", "message": "timeout timeout",
         "body": json.dumps({"items": [True, None, {"message": "동기"}]})},
        {"trace": "beta", "event": "open", "level": "info", "message": "other"},
        {"trace": "alpha", "event": "read", "level": "error", "message": "timeout", "status": None},
        {"message": "standalone two", "status": ""},
        {"trace": "alpha", "event": "write", "level": "error", "message": "saved"},
        {"trace": "alpha", "event": "close", "level": "info", "message": "done"},
    )


@dataclass(frozen=True)
class PaintedClick:
    """Point at readable frame text rather than internal widget coordinates."""

    row_contains: str
    target: str
    button: int = 0


@dataclass(frozen=True)
class Checkpoint:
    """Capture a public frame without emitting a viewer event."""

    name: str


class InteractiveHost(FakeHost):
    """Feed keys, geometry changes, and clicks against the last painted frame."""

    def __init__(self, events, **kwargs) -> None:
        super().__init__(events, **kwargs)
        self.checkpoints: dict[str, str] = {}

    def read_event(self) -> str | None:
        event = next(self._events, None)
        while isinstance(event, Checkpoint):
            self.checkpoints[event.name] = self.frames[-1]
            event = next(self._events, None)
        if isinstance(event, tuple):
            self._size = event
            return "idle"
        if isinstance(event, PaintedClick):
            for row, line in enumerate(strip_ansi(self.frames[-1]).splitlines(), 1):
                if event.row_contains in line and event.target in line:
                    column = _text_cells(line[:line.index(event.target)]) + 1
                    return f"mouse\t{event.button}\t{column}\t{row}\tpress"
            raise AssertionError(f"Click target {event!r} absent from frame:\n{self.frames[-1]}")
        return event
