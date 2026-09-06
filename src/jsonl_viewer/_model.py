"""Private immutable snapshot and transient view-state values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class Record:
    source_line: int
    utf8_bytes: int
    value: JSONValue


@dataclass(frozen=True, slots=True)
class Snapshot:
    records: tuple[Record, ...]
    source_utf8_bytes: int


@dataclass(frozen=True, slots=True)
class InputDiagnostic:
    summary: str
    detail: str
    source_line: int | None = None


class ViewMode(Enum):
    SIMPLE = "Simple"
    VERBOSE = "Verbose"


@dataclass(frozen=True, slots=True)
class SearchState:
    field: str
    query: str
    matches: tuple[int, ...]
    current_index: int | None

    @property
    def current_record_index(self) -> int | None:
        if self.current_index is None:
            return None
        return self.matches[self.current_index]


@dataclass(frozen=True, slots=True)
class PromptState:
    """An uncommitted search-field, search-query, or goto edit."""

    kind: str
    buffer: str = ""
    cursor: int = 0
    field: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ViewState:
    selected_index: int = 0
    record_line_offset: int = 0
    mode: ViewMode = ViewMode.SIMPLE
    search: SearchState | None = None
    help_visible: bool = False
    message: str | None = None
    message_is_error: bool = False
    prompt: PromptState | None = None


@dataclass(frozen=True, slots=True)
class RenderResult:
    text: str
    body_rows: int
    selected_line_count: int
