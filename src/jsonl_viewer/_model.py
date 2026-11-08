"""Private immutable snapshot and transient view-state values."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONPath: TypeAlias = tuple[str | int, ...]
MatchPaths: TypeAlias = tuple[JSONPath, ...]


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
class Occurrence:
    record_index: int
    path: JSONPath
    kind: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class SearchState:
    query: str
    occurrences: tuple[Occurrence, ...]
    current_index: int | None

    @property
    def current_occurrence(self) -> Occurrence | None:
        return None if self.current_index is None else self.occurrences[self.current_index]

    @property
    def current_record_index(self) -> int | None:
        if self.current_index is None:
            return None
        return self.occurrences[self.current_index].record_index

    def paths_for_record(self, record_index: int) -> MatchPaths:
        start = bisect_left(self.occurrences, record_index, key=lambda hit: hit.record_index)
        end = bisect_right(self.occurrences, record_index, key=lambda hit: hit.record_index)
        return tuple(hit.path for hit in self.occurrences[start:end])

    def has_record(self, record_index: int) -> bool:
        position = bisect_left(self.occurrences, record_index, key=lambda hit: hit.record_index)
        return position < len(self.occurrences) and self.occurrences[position].record_index == record_index


@dataclass(frozen=True, slots=True)
class PromptState:
    """An uncommitted search-query or goto edit."""

    kind: str
    buffer: str = ""
    cursor: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FoldIdentity:
    """A container in one immutable source record, including decoded children."""

    record_index: int
    path: JSONPath


@dataclass(frozen=True, slots=True)
class CursorPosition:
    record_index: int
    line_index: int
    column: int


@dataclass(frozen=True, slots=True)
class SelectionPoint:
    """A cluster's body-relative cell position, independent of fixed gutters."""

    record_index: int
    line_index: int
    column: int


@dataclass(frozen=True, slots=True)
class TextSelection:
    anchor: SelectionPoint
    extent: SelectionPoint
    anchor_width: int
    extent_width: int
    records: tuple[int, ...]
    pointer_column: int
    pointer_row: int
    moved: bool = False
    dragging: bool = True
    edge_intent: bool = False


@dataclass(frozen=True, slots=True)
class PaintedSpan:
    """A painted copyable segment with intact clusters and body coordinates."""

    screen_column: int
    column: int
    text: str
    cells: int


@dataclass(frozen=True, slots=True)
class BodyLine:
    """One painted projection row, without allocating a character map."""

    record_index: int
    line_index: int
    screen_row: int
    cells: int
    last_in_record: bool = False
    spans: tuple[PaintedSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionTextRow:
    record_index: int
    line_index: int
    parts: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class VisibleCharacter:
    position: CursorPosition
    screen_row: int
    text: str
    container: FoldIdentity | None = None
    delimiter: str | None = None
    search_focus: bool = False
    property: FoldIdentity | None = None
    key_anchor: bool = False
    screen_column: int = 0
    screen_width: int = 1

@dataclass(frozen=True, slots=True)
class PropertyMetadata:
    identity: FoldIdentity
    parent: JSONPath


@dataclass(frozen=True, slots=True)
class ContainerMetadata:
    identity: FoldIdentity
    nonempty: bool
    folded: bool


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
    reveal_match: bool = False
    cursor: CursorPosition | None = None
    preferred_column: int | None = None
    folds: frozenset[FoldIdentity] = frozenset()
    focus_container: FoldIdentity | None = None
    focus_property: FoldIdentity | None = None
    horizontal_offset: int = 0
    reveal_cursor: bool = False
    gutter_columns: int = 0
    selection: TextSelection | None = None
    copy_pending: bool = False
    copy_notice: bool = False
    selection_count: int = 0


@dataclass(frozen=True, slots=True)
class RenderResult:
    text: str
    body_rows: int
    selected_line_count: int
    record_line_offset: int = 0
    cursor: CursorPosition | None = None
    characters: tuple[VisibleCharacter, ...] = ()
    containers: tuple[ContainerMetadata, ...] = ()
    columns: int = 80
    rows: int = 24
    color: bool = False
    properties: tuple[PropertyMetadata, ...] = ()
    idle_text: str | None = None
    navigable_rows: tuple[tuple[int, tuple[int, ...]], ...] = ()
    logical_characters: tuple[VisibleCharacter, ...] = ()
    horizontal_offset: int = 0
    max_horizontal_offset: int = 0
    body_width: int = 74
    gutter_columns: int = 0
    body_lines: tuple[BodyLine, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryResult:
    filtered: tuple[int, ...]
    items: tuple[int, ...]
    search: SearchState | None
    matching_records: int = 0
    unavailable: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BrowserState:
    filters: tuple[frozenset[int], ...] = ()
    query: str | None = None
    scope: int = -1
    result: QueryResult | None = None
    selected: int = 0
    section: int = 0
    focus: str = "detail"
    layout: str = "single"
    list_query: str = ""
    info_offset: int = 0
    appearance: str = "dark"
    navigation: tuple[tuple[int, int, ViewState], ...] = ()
    detail: ViewState = ViewState()
    panel: str | None = None
    panel_index: int = 0
    field: int = 0
    draft_filters: tuple[frozenset[int], ...] = ()
    draft_options: frozenset[int] = frozenset()
    draft_query: PromptState = PromptState("search_query")
    draft_scope: int = -1
    reset_all: bool = False
    error: str | None = None
    notice: str | None = None
    diagnostic: InputDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class BrowserHit:
    kind: str
    index: int
    row: int
    column: int
    width: int


@dataclass(frozen=True, slots=True)
class BrowserFrame:
    text: str
    detail: RenderResult | None = None
    detail_row: int = 0
    detail_column: int = 0
    hits: tuple[BrowserHit, ...] = ()
    columns: int = 80
    rows: int = 24
    color: bool = False
    body_row: int = 0
    info_max_offset: int = 0
