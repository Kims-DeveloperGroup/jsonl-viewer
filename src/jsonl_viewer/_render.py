"""Deterministic semantic ANSI/plain rendering for transient view state."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Iterable

from ._cell_layout import RowLayout, character_cells as _character_cells, clusters as _clusters, text_cells as _text_cells
from ._json import Expansion, ExpansionBudget, SkippedExpansion, expand_json_string
from ._model import (
    ContainerMetadata,
    PropertyMetadata,
    CursorPosition,
    FoldIdentity,
    VisibleCharacter,
    InputDiagnostic,
    JSONPath,
    JSONValue,
    Occurrence,
    PromptState,
    Record,
    RenderResult,
    Snapshot,
    ViewMode,
    ViewState,
)
from .contracts import ViewerSpec


SIMPLE_CONTENT_PREVIEW_BYTES = 4_096
VERBOSE_STRING_PREVIEW_BYTES = 65_536
MAX_COLUMNS = 240
MAX_ROWS = 100
MIN_COLUMNS = 12
MIN_ROWS = 4

_ANSI = {
    "chrome": "1;36",
    "gutter": "2;90",
    "key": "34",
    "string": "32",
    "number": "35",
    "literal": "33",
    "current": "1;36",
    "match_current": "1;30;43",
    "match_other": "4;33",
    "warning": "1;33",
    "error": "1;31",
    "muted": "2;90",
    "footer": "2;36",
    "plain": "0",
}
_SGR = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class _Segment:
    text: str
    role: str = "plain"
    navigable: bool = False
    container: JSONPath | None = None
    delimiter: str | None = None
    nonempty: bool = False
    folded: bool = False
    property: JSONPath | None = None
    key_anchor: bool = False
    logical_column: int | None = None


@dataclass(frozen=True, slots=True)
class _DisplayLine:
    segments: tuple[_Segment, ...]
    path: JSONPath


@dataclass(slots=True)
class _ProjectionFacts:
    properties: list[tuple[JSONPath, JSONPath]] = field(default_factory=list)
    expanded_strings: int = 0
    skipped_expansions: int = 0
    truncated_leaves: int = 0
    truncated_retained_utf8_bytes: int = 0
    truncated_full_utf8_bytes: int = 0
    content_preview: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class _ProjectionSummary:
    properties: tuple[tuple[JSONPath, JSONPath], ...]
    expanded_strings: int
    skipped_expansions: int
    truncated_leaves: int
    truncated_retained_utf8_bytes: int
    truncated_full_utf8_bytes: int
    content_preview: tuple[int, int] | None


_CACHE_RECORDS = 2
_CACHE_TEXT_BYTES = 8 * 1024 * 1024
_CACHE_ROWS = 8192
_CACHE_SEGMENTS = 65536
_CACHE_CHECKPOINTS = 65536


@dataclass(slots=True)
class _RecordProjection:
    key: tuple
    lines: tuple[_DisplayLine, ...]
    facts: _ProjectionSummary
    widths: tuple[int, ...]
    containers: tuple[ContainerMetadata, ...]
    navigable_rows: tuple[int, ...]
    key_rows: dict[JSONPath, int]
    container_rows: dict[JSONPath, int]
    match_row: int | None
    text_bytes: int
    segment_count: int
    layouts: dict[int, RowLayout] = field(default_factory=dict)
    checkpoints: int = 0


class RenderSession:
    """Explicit bounded derived caches; its engine owner supplies call lifetime."""

    def __init__(self) -> None:
        self._context: tuple[ViewerSpec, Snapshot | None] | None = None
        self._records: OrderedDict[int, _RecordProjection] = OrderedDict()
        self._text_bytes = self._rows = self._segments = self._checkpoints = 0

    def bind(self, spec: ViewerSpec, snapshot: Snapshot | None) -> None:
        if self._context is None or self._context[0] is not spec or self._context[1] is not snapshot:
            self._records.clear()
            self._text_bytes = self._rows = self._segments = self._checkpoints = 0
            self._context = (spec, snapshot)

    def cache_info(self) -> tuple[int, int, int, int, int]:
        """Record slots, UTF-8 bytes, rows, segments and sparse checkpoints."""
        return len(self._records), self._text_bytes, self._rows, self._segments, self._checkpoints

    def _evict(self, index: int) -> None:
        old = self._records.pop(index)
        self._text_bytes -= old.text_bytes
        self._rows -= len(old.lines)
        self._segments -= old.segment_count
        self._checkpoints -= old.checkpoints

    def projection(self, record: Record, index: int, spec: ViewerSpec, state: ViewState) -> _RecordProjection:
        active = state.search.current_occurrence if state.search is not None else None
        if active is not None and active.record_index != index:
            active = None
        folds = frozenset(fold.path for fold in state.folds if fold.record_index == index)
        key = (state.mode, active, folds)
        cached = self._records.get(index)
        if cached is not None and cached.key == key:
            self._records.move_to_end(index)
            return cached
        if cached is not None:
            self._evict(index)
        projection = _build_projection(record, index, spec, state.mode, active, folds, key)
        if (projection.text_bytes > _CACHE_TEXT_BYTES or len(projection.lines) > _CACHE_ROWS
                or projection.segment_count > _CACHE_SEGMENTS):
            return projection
        while self._records and (len(self._records) >= _CACHE_RECORDS
                or self._text_bytes + projection.text_bytes > _CACHE_TEXT_BYTES
                or self._rows + len(projection.lines) > _CACHE_ROWS
                or self._segments + projection.segment_count > _CACHE_SEGMENTS):
            self._evict(next(iter(self._records)))
        self._records[index] = projection
        self._text_bytes += projection.text_bytes
        self._rows += len(projection.lines)
        self._segments += projection.segment_count
        return projection

    def layout(self, index: int, projection: _RecordProjection, row: int) -> RowLayout:
        cached = projection.layouts.get(row)
        if cached is not None:
            return cached
        layout = RowLayout.build(segment.text for segment in projection.lines[row].segments)
        if (self._records.get(index) is projection
                and self._checkpoints + layout.checkpoints <= _CACHE_CHECKPOINTS):
            projection.layouts[row] = layout
            projection.checkpoints += layout.checkpoints
            self._checkpoints += layout.checkpoints
        return layout


def strip_ansi(value: str) -> str:
    """Return semantic text without renderer-owned SGR sequences."""

    return _SGR.sub("", value)


def _neutralize_character(character: str) -> str:
    codepoint = ord(character)
    category = unicodedata.category(character)
    if (
        codepoint < 32
        or 0x7F <= codepoint <= 0x9F
        or category in {"Cc", "Cf", "Cs"}
        or character in {"\u2028", "\u2029"}
    ):
        width = 4 if codepoint <= 0xFFFF else 8
        return f"\\u{codepoint:0{width}x}"
    return character


def _neutralize_text(value: str) -> str:
    return "".join(_neutralize_character(character) for character in value)


def _safe_json_token(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    return "".join(_neutralize_character(character) for character in encoded)


def _take_cells(value: str, maximum: int) -> tuple[str, int, bool]:
    if maximum <= 0:
        return "", 0, bool(value)
    result: list[str] = []
    used = 0
    for character in value:
        width = _character_cells(character)
        if used + width > maximum:
            return "".join(result), used, True
        result.append(character)
        used += width
    return "".join(result), used, False


def _clip_segments(
    segments: Iterable[_Segment],
    maximum: int,
) -> tuple[tuple[_Segment, ...], bool]:
    materialized = tuple(segments)
    if sum(_text_cells(segment.text) for segment in materialized) <= maximum:
        return materialized, False
    remaining = max(0, maximum - 1)
    result: list[_Segment] = []
    for segment in materialized:
        if remaining <= 0:
            break
        piece, used, clipped = _take_cells(segment.text, remaining)
        if piece:
            result.append(replace(segment, text=piece))
        remaining -= used
        if clipped:
            break
    if maximum > 0:
        result.append(_Segment("…", "muted"))
    return tuple(result), True


def _paint(segments: Iterable[_Segment], *, color: bool) -> str:
    if not color:
        return "".join(segment.text for segment in segments)
    return "".join(
        (f"\x1b[{_ANSI.get(segment.role, _ANSI['plain'])}m{segment.text}\x1b[0m")
        for segment in segments
    )


def _clip_text(value: str, width: int) -> str:
    piece, _, clipped = _take_cells(value, width)
    if not clipped:
        return piece
    if width <= 1:
        return "…"[:width]
    piece, _, _ = _take_cells(value, width - 1)
    return piece + "…"


def _utf8_prefix(value: str, maximum: int) -> tuple[str, int, int]:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        # JSON escapes can retain isolated surrogates. Count the printable
        # escape emitted by _safe_json_token, preserving the original leaf.
        retained = complete = end = 0
        for index, character in enumerate(value):
            complete += len(character.encode("utf-8", errors="backslashreplace"))
            if complete <= maximum:
                retained, end = complete, index + 1
        return value[:end], retained, complete
    if len(encoded) <= maximum:
        return value, len(encoded), len(encoded)
    retained = encoded[:maximum]
    while retained:
        try:
            prefix = retained.decode("utf-8", errors="strict")
            return prefix, len(retained), len(encoded)
        except UnicodeDecodeError as exc:
            retained = retained[: exc.start]
    return "", 0, len(encoded)


def _string_segments(
    value: str,
    *,
    maximum: int,
    path: JSONPath,
    content_field: str,
    facts: _ProjectionFacts,
) -> tuple[_Segment, ...]:
    prefix, retained, complete = _utf8_prefix(value, maximum)
    token = _safe_json_token(prefix)
    if retained == complete:
        return (_Segment(token, "string"),)
    facts.truncated_leaves += 1
    facts.truncated_retained_utf8_bytes += retained
    facts.truncated_full_utf8_bytes += complete
    if path == (content_field,):
        facts.content_preview = (retained, complete)
    marker = f"[truncated {retained}/{complete} UTF-8 bytes] "
    return (
        _Segment(marker, "muted"),
        _Segment(token[:-1], "string"),
        _Segment("…", "muted"), _Segment('"', "string"),
    )


def _focused_token(
    text: str, hit: Occurrence, role: str, *, quoted: bool = True,
) -> tuple[_Segment, ...]:
    """Escape bounded context and keep combining marks with their base cell."""
    focus_start, focus_end = hit.start, hit.end
    while focus_start > 0 and unicodedata.combining(text[focus_start]):
        focus_start -= 1
    while focus_end < len(text) and unicodedata.combining(text[focus_end]):
        focus_end += 1
    start, end = max(0, focus_start - 32), min(len(text), focus_end + 32)

    def safe(piece: str) -> str:
        return _safe_json_token(piece)[1:-1] if quoted else _neutralize_text(piece)

    focused = text[focus_start:focus_end]
    leading_marks = 0
    while leading_marks < len(focused) and _character_cells(focused[leading_marks]) == 0:
        leading_marks += 1
    # A match beginning with an unattached mark has no readable cursor cell.
    # Escape only that leading run; keep ordinary base/mark clusters intact and
    # leave source offsets, occurrence counts, and the immutable text unchanged.
    focused_display = (
        json.dumps(focused[:leading_marks], ensure_ascii=True)[1:-1]
        + safe(focused[leading_marks:])
    )

    result: list[_Segment] = []
    if quoted:
        result.append(_Segment('"', role))
    if start:
        result.append(_Segment("…", "muted"))
    result.extend((
        _Segment(safe(text[start:focus_start]), role), _Segment("⟦", "current"),
        _Segment(focused_display, "match_current"),
        _Segment("⟧", "current"), _Segment(safe(text[focus_end:end]), role),
    ))
    if end < len(text):
        result.append(_Segment("…", "muted"))
    if quoted:
        result.append(_Segment('"', role))
    return tuple(result)


def _data_segments(
    segments: tuple[_Segment, ...], container: JSONPath | None,
) -> tuple[_Segment, ...]:
    return tuple(
        replace(segment, navigable=True, container=container)
        if segment.role not in {"current", "warning", "muted"} else segment
        for segment in segments
    )


def _key_segments(tokens: tuple[_Segment, ...], key: str, owner: JSONPath,
                  parent: JSONPath) -> tuple[_Segment, ...]:
    """Mark the first displayed key character without interpreting JSON text."""
    if key and not _character_cells(key[0]):
        # A leading combining mark has no independent display cell. Retain its
        # readable escaped spelling so sibling focus can land inside the key.
        offset = 0
        escaped: list[_Segment] = []
        for token in tokens:
            local = 1 - offset
            text = token.text
            if 0 <= local < len(text) and text[local] == key[0]:
                text = text[:local] + f"\\u{ord(key[0]):04x}" + text[local + 1:]
            escaped.append(replace(token, text=text))
            offset += len(token.text)
        tokens = tuple(escaped)
    anchor = 0
    if key:
        offset = 0
        for token in tokens:
            local = 1 if offset == 0 and token.text.startswith('"') else 0
            if token.role not in {"current", "warning", "muted"}:
                for cluster in _clusters(token.text[local:]):
                    if _text_cells(cluster):
                        anchor = offset + local
                        break
                    local += len(cluster)
                else:
                    offset += len(token.text)
                    continue
                break
            offset += len(token.text)
    offset = 0
    result: list[_Segment] = []
    for token in tokens:
        base = replace(token, navigable=token.role not in {"current", "warning", "muted"},
                       container=parent, property=owner)
        local = anchor - offset
        if 0 <= local < len(token.text):
            if local:
                result.append(replace(base, text=token.text[:local]))
            cluster = next(iter(_clusters(token.text[local:])))
            result.append(replace(base, text=cluster, key_anchor=True))
            rest = token.text[local + len(cluster):]
            if rest:
                result.append(replace(base, text=rest))
        else:
            result.append(base)
        offset += len(token.text)
    result.append(_Segment(": ", navigable=True, container=parent, property=owner))
    return tuple(result)


def _format_value(
    value: JSONValue,
    *,
    indent: int,
    display_depth: int,
    path: JSONPath,
    prefix: tuple[_Segment, ...] = (),
    string_limit: int,
    content_field: str,
    budget: ExpansionBudget,
    facts: _ProjectionFacts,
    active: Occurrence | None = None,
    folds: frozenset[JSONPath] = frozenset(),
    parent: JSONPath | None = None,
    owner: JSONPath | None = None,
) -> list[_DisplayLine]:
    leading = (_Segment(" " * indent),) + prefix
    if isinstance(value, (dict, list)):
        opening, closing = ("{", "}") if isinstance(value, dict) else ("[", "]")
        folded = bool(value) and path in folds
        opener = _Segment(opening, navigable=True, container=path,
                          delimiter="open", nonempty=bool(value), folded=folded, property=owner)
        closer = replace(opener, text=closing, delimiter="close")
        if not value:
            return [_DisplayLine(leading + (opener, closer), path)]
        lines = [_DisplayLine(leading + (opener,), path)]
        items = tuple(value.items()) if isinstance(value, dict) else tuple(enumerate(value))
        for index, (key, child) in enumerate(items):
            child_path = path + (key,)
            child_prefix: tuple[_Segment, ...] = ()
            if isinstance(value, dict):
                facts.properties.append((child_path, path))
                key_token = (
                    _focused_token(key, active, "key")
                    if active is not None and active.path == child_path
                    and active.kind == "key" and active.text == key
                    else (_Segment(_safe_json_token(key), "key"),)
                )
                child_prefix = _key_segments(key_token, key, child_path, path)
            child_lines = _format_value(
                child, indent=indent + 2, display_depth=display_depth + 1,
                path=child_path, prefix=child_prefix, string_limit=string_limit,
                content_field=content_field, budget=budget, facts=facts,
                active=active, folds=folds, parent=path,
                owner=child_path if isinstance(value, dict) else owner,
            )
            if index + 1 < len(items):
                last = child_lines[-1]
                child_lines[-1] = _DisplayLine(
                    last.segments + (_Segment(",", navigable=True, container=path, property=child_path if isinstance(value, dict) else owner),),
                    last.path,
                )
            lines.extend(child_lines)
        lines.append(_DisplayLine((_Segment(" " * indent), closer), path))
        # Traverse first even when folded: folding must not change expansion
        # budgets, projection facts, or acceptance of later encoded strings.
        if folded:
            return [_DisplayLine(leading + (opener, _Segment("..."), closer), path)]
        return lines
    if isinstance(value, str):
        expansion = expand_json_string(value, display_depth=display_depth, budget=budget)
        if isinstance(expansion, Expansion):
            facts.expanded_strings += 1
            cue = _Segment(f"[expanded JSON string ×{expansion.layers}] ", "muted")
            return _format_value(
                expansion.value, indent=indent, display_depth=display_depth,
                path=path, prefix=prefix + (cue,), string_limit=string_limit,
                content_field=content_field, budget=budget, facts=facts,
                active=active, folds=folds, parent=parent, owner=owner,
            )
        if isinstance(expansion, SkippedExpansion):
            facts.skipped_expansions += 1
            leading += (_Segment(f"[JSON expansion skipped: {expansion.reason}] ", "warning"),)
        if (active is not None and active.path == path and active.kind == "value"
                and active.text == value):
            token = _focused_token(value, active, "string")
        else:
            token = _string_segments(value, maximum=string_limit, path=path,
                                     content_field=content_field, facts=facts)
    elif value is None:
        token = (_Segment("null", "literal"),)
    elif type(value) is bool:
        token = (_Segment("true" if value else "false", "literal"),)
    else:
        token = (_Segment(json.dumps(value, allow_nan=False), "number"),)
    if (not isinstance(value, str) and active is not None and active.path == path
            and active.kind == "value" and active.text == token[0].text):
        token = _focused_token(active.text, active, token[0].role, quoted=False)
    return [_DisplayLine(leading + tuple(replace(segment, property=owner) for segment in _data_segments(token, parent)), path)]


def _project_record(
    record: Record,
    spec: ViewerSpec,
    mode: ViewMode,
) -> tuple[JSONValue, int, int]:
    value = record.value
    if not isinstance(value, dict):
        return value, 0, VERBOSE_STRING_PREVIEW_BYTES
    ordered: dict[str, JSONValue] = {}
    for field in spec.primary_fields:
        if field in value:
            ordered[field] = value[field]
    if mode is ViewMode.VERBOSE:
        for field, item in value.items():
            if field not in ordered:
                ordered[field] = item
        return ordered, 0, VERBOSE_STRING_PREVIEW_BYTES
    return ordered, len(value) - len(ordered), SIMPLE_CONTENT_PREVIEW_BYTES


def _format_record(
    record: Record,
    spec: ViewerSpec,
    mode: ViewMode,
    active: Occurrence | None = None,
    folds: frozenset[JSONPath] = frozenset(),
) -> tuple[list[_DisplayLine], _ProjectionFacts]:
    value, _, limit = _project_record(record, spec, mode)
    budget = ExpansionBudget()
    facts = _ProjectionFacts()
    lines = _format_value(
        value,
        indent=0,
        display_depth=1,
        path=(),
        string_limit=limit,
        content_field=spec.content_field,
        budget=budget,
        facts=facts,
        active=active,
        folds=folds,
    )
    hidden_by_fold = active is not None and any(
        active.path[:len(path)] == path
        and not (active.kind == "key" and active.path == path) for path in folds
    )
    if active is not None and not hidden_by_fold and not any(
        segment.role == "match_current" for line in lines for segment in line.segments
    ):
        # Raw/normalized hits and independently exhausted display expansion
        # budgets still get an exact bounded excerpt and an addressable row.
        label = active.kind if active.kind in {"raw", "normalized"} else "decoded"
        lines.append(_DisplayLine(
            (_Segment(f"[{label} search excerpt]", "muted"),), active.path,
        ))
        containing_paths = [segment.container for line in lines for segment in line.segments
                            if segment.delimiter == "open" and segment.container is not None
                            and active.path[:len(segment.container)] == segment.container
                            and not (active.kind == "key" and active.path == segment.container)]
        container = max(containing_paths, key=len) if containing_paths else None
        owners = [path for path, _ in facts.properties if active.path[:len(path)] == path]
        owner = max(owners, key=len) if owners else None
        lines.append(_DisplayLine(
            tuple(replace(segment, property=owner) for segment in
                  _data_segments(_focused_token(active.text, active, "string"), container)), active.path,
        ))
    return lines, facts


def record_line_count(record: Record, spec: ViewerSpec, mode: ViewMode) -> int:
    lines, _ = _format_record(record, spec, mode)
    return len(lines)


def _header_lines(
    spec: ViewerSpec,
    state: ViewState,
    *,
    width: int,
) -> list[tuple[str, str]]:
    title = _neutralize_text(spec.title).upper()
    session = _neutralize_text(spec.session_id)
    conversation_label = _neutralize_text(spec.conversation_label)
    conversation_id = _neutralize_text(spec.conversation_id)
    conversation = f"{conversation_label}: {conversation_id}"
    if spec.conversation_subject is not None:
        subject = _neutralize_text(spec.conversation_subject)
        conversation = f"{conversation} — {subject}"
    agent = _neutralize_text(spec.agent_id)
    mode = state.mode.value.upper()
    if width >= 100:
        return [
            (
                _clip_text(
                    f"{title} • READ ONLY • {mode} | Session: {session} | "
                    f"{conversation} | Agent: {agent}",
                    width,
                ),
                "chrome",
            )
        ]
    if width >= 48:
        return [
            (_clip_text(f"{title} • READ ONLY • {mode}", width), "chrome"),
            (
                _clip_text(
                    f"Session: {session} • {conversation} • Agent: {agent}",
                    width,
                ),
                "chrome",
            ),
        ]
    return [
        (_clip_text(f"READ ONLY • {mode}", width), "chrome"),
        (
            _clip_text(f"S:{session} • {conversation} • A:{agent}", width),
            "chrome",
        ),
    ]


def _help_body(width: int) -> list[tuple[str, str]]:
    values = (
        ("Help — immutable snapshot; commands never edit, replay, or retry.", "chrome"),
        ("h/l         move cursor left/right; reveal full rows; wrap actual ends", "plain"),
        ("←/→         pan JSON by half its width; left/right commands", "plain"),
        ("j/k         move cursor down/up within visible JSON", "plain"),
        ("J/K next/previous sibling property key", "plain"),
        ("Cursor blinks every 500 ms while idle", "plain"),
        ("↑/↓         previous/next source record; wrap endpoints", "plain"),
        ("Enter/fold  collapse/expand container: {...} / [...]", "plain"),
        ("? / help    show or dismiss help", "plain"),
        ("PgUp/PgDn   scroll one record, then cycle records", "plain"),
        ("g LINE      go to an exact source-record line", "plain"),
        ("/ QUERY     search all text (keys and complete values)", "plain"),
        ("/           open Search query in key mode", "plain"),
        ("n / N       next / previous keyword occurrence (⟦active⟧)", "plain"),
        ("m           Simple / Verbose mode", "plain"),
        ("Esc         cancel help/search/status; otherwise close", "plain"),
        ("q           close and discard all transient viewer state", "plain"),
    )
    return [(_clip_text(text, width), role) for text, role in values]


def _status_body(
    diagnostic: InputDiagnostic | None,
    snapshot: Snapshot | None,
    *,
    width: int,
) -> list[tuple[str, str]]:
    if diagnostic is not None:
        return [
            (_clip_text("! INPUT ERROR — " + diagnostic.summary, width), "error"),
            (_clip_text(_neutralize_text(diagnostic.detail), width), "error"),
            (
                _clip_text(
                    "Source bytes are unchanged. Press ? for help or q to close.", width
                ),
                "muted",
            ),
        ]
    assert snapshot is not None
    if not snapshot.records:
        return [
            (_clip_text("(empty immutable JSONL snapshot)", width), "muted"),
            (_clip_text("No source records to navigate or search.", width), "muted"),
        ]
    return []


def _record_marker(index: int, state: ViewState, *, matched: bool) -> tuple[str, str]:
    search = state.search
    if search is not None and search.current_record_index == index:
        return "@", "current"
    if matched:
        return "*", "gutter"
    if index == state.selected_index:
        return ">", "current"
    return " ", "gutter"


@dataclass(frozen=True, slots=True)
class _RecordLine:
    segments: tuple[_Segment, ...]
    record_index: int
    line_index: int
    gutter_columns: int = 0
    layout: RowLayout | None = None
    body_cells: int = 0


def _build_projection(record: Record, index: int, spec: ViewerSpec, mode: ViewMode,
                      active: Occurrence | None, folds: frozenset[JSONPath], key: tuple) -> _RecordProjection:
    logical, facts = _format_record(record, spec, mode, active, folds)
    visible_properties = {segment.property for line in logical for segment in line.segments if segment.key_anchor}
    summary = _ProjectionSummary(
        tuple((path, parent) for path, parent in facts.properties if path in visible_properties),
        facts.expanded_strings, facts.skipped_expansions, facts.truncated_leaves,
        facts.truncated_retained_utf8_bytes, facts.truncated_full_utf8_bytes, facts.content_preview,
    )
    widths: list[int] = []
    containers: list[ContainerMetadata] = []
    navigable: list[int] = []
    key_rows: dict[JSONPath, int] = {}
    container_rows: dict[JSONPath, int] = {}
    match_row = None
    text_bytes = segments = 0
    for row, line in enumerate(logical):
        cells = tuple(_text_cells(segment.text) for segment in line.segments)
        widths.append(sum(cells))
        if any(segment.navigable and width for segment, width in zip(line.segments, cells)):
            navigable.append(row)
        for segment in line.segments:
            text_bytes += len(segment.text.encode("utf-8"))
            segments += 1
            if segment.delimiter == "open":
                assert segment.container is not None
                containers.append(ContainerMetadata(FoldIdentity(index, segment.container), segment.nonempty, segment.folded))
                container_rows[segment.container] = row
            if segment.key_anchor and segment.property is not None:
                key_rows[segment.property] = row
            if segment.role == "match_current" and match_row is None:
                match_row = row
    return _RecordProjection(key, tuple(logical), summary, tuple(widths), tuple(containers),
                             tuple(navigable), key_rows, container_rows, match_row, text_bytes, segments)


def _record_line(record: Record, index: int, row: int, projection: _RecordProjection,
                 state: ViewState, gutter_width: int, session: RenderSession) -> _RecordLine:
    matched = state.search is not None and state.search.has_record(index)
    marker, marker_role = _record_marker(index, state, matched=matched)
    gutter = (_Segment(marker, marker_role), _Segment(" ", "gutter"),
              _Segment(str(record.source_line).rjust(gutter_width), "gutter"), _Segment(" │ ", "gutter"))
    return _RecordLine(gutter + projection.lines[row].segments, index, row, gutter_width + 5,
                       session.layout(index, projection, row), projection.widths[row])


def _window_line(line: _RecordLine, offset: int, width: int, *, overflow_cue: bool = True,
                 focus_span: tuple[int, int] | None = None) -> _RecordLine:
    """Slice body cells without splitting clusters or changing fixed gutters."""
    gutter, segments = line.segments[:4], line.segments[4:]
    layout = line.layout or RowLayout.build(segment.text for segment in segments)
    right_clipped = overflow_cue and layout.cells > offset + width
    if (focus_span is not None and offset <= focus_span[0]
            and focus_span[1] <= offset + width
            and focus_span[1] > offset + width - 1):
        right_clipped = False
    end = offset + max(0, width - int(right_clipped))
    result: list[_Segment] = list(gutter)
    current_index = None
    piece_start = piece_begin = piece_end = 0
    painted = 0

    def append_piece() -> None:
        if current_index is not None:
            result.append(replace(segments[current_index], text=segments[current_index].text[piece_begin:piece_end],
                                  logical_column=line.gutter_columns + piece_start))

    for index, start, begin, finish, cells in layout.window(offset, end):
        if current_index != index:
            append_piece()
            current_index = index
            piece_start, piece_begin = start, begin
            gap = start - offset - painted
            if gap > 0:
                result.append(_Segment(" " * gap))
                painted += gap
        piece_end = finish
        painted += cells
    append_piece()
    if right_clipped:
        result.append(_Segment("…", "muted"))
    return replace(line, segments=tuple(result), layout=None)


def _focus_prefix_window(line: _RecordLine, offset: int, width: int,
                         first_cluster_cells: int) -> _RecordLine:
    """Keep a bounded marked keyword prefix with at least one intact cluster."""
    window = _window_line(line, offset, width - 1,
                          overflow_cue=width >= first_cluster_cells + 3)
    return replace(window, segments=window.segments + (_Segment("⟧", "current"),))


def _character_stream(line: _RecordLine, screen_row: int) -> Iterable[VisibleCharacter]:
    column = screen_column = 0
    for segment in line.segments:
        if segment.logical_column is not None:
            column = segment.logical_column
        for cluster in _clusters(segment.text):
            width = _text_cells(cluster)
            if segment.navigable and width:
                yield VisibleCharacter(
                    CursorPosition(line.record_index, line.line_index, column),
                    screen_row, cluster,
                    None if segment.container is None else FoldIdentity(line.record_index, segment.container),
                    segment.delimiter, segment.role == "match_current",
                    None if segment.property is None else FoldIdentity(line.record_index, segment.property),
                    segment.key_anchor, screen_column, width,
                )
            column += width
            screen_column += width


def _visible_characters(line: _RecordLine, screen_row: int) -> tuple[VisibleCharacter, ...]:
    return tuple(_character_stream(line, screen_row))


def _logical_characters(line: _RecordLine, screen_row: int, state: ViewState,
                        body_width: int) -> tuple[VisibleCharacter, ...]:
    """Seek bounded candidates without allocating discarded row characters."""
    segments = line.segments[4:]
    layout = line.layout or RowLayout.build(segment.text for segment in segments)
    retained: dict[int, tuple[int, int, int, int]] = {}

    def retain(index: int, span: tuple[int, int, int, int]) -> None:
        column, begin, finish, cells = span
        if cells and segments[index].navigable:
            retained[layout.starts[index] + column] = (index, begin, finish, cells)

    def retain_window(start: int, end: int) -> None:
        for index, column, begin, finish, cells in layout.window(start, end):
            if segments[index].navigable:
                retained[column] = (index, begin, finish, cells)

    retain_window(state.horizontal_offset - 2, state.horizontal_offset + body_width + 4)
    preferred = state.preferred_column
    if preferred is None and state.cursor is not None:
        preferred = state.cursor.column
    closest = None
    first = None
    first_match = True
    for index, (segment, part) in enumerate(zip(segments, layout.parts)):
        if not segment.navigable or not part.cells:
            continue
        beginning = part.nearest(0)
        ending = part.nearest(part.cells)
        if beginning is not None:
            retain(index, beginning)
            if first is None:
                first = layout.starts[index] + beginning[0]
        if ending is not None:
            retain(index, ending)
        if segment.role == "match_current" and first_match:
            if beginning is not None:
                retain(index, beginning)
            first_match = False
        if preferred is not None:
            candidate = part.nearest(preferred - line.gutter_columns - layout.starts[index])
            if candidate is not None:
                column = layout.starts[index] + candidate[0] + line.gutter_columns
                distance = (abs(column - preferred), column)
                if closest is None or distance < closest[0]:
                    closest = (distance, index, candidate)
    if first is not None:
        retain_window(first, first + 4)
    if closest is not None:
        retain(closest[1], closest[2])
    if state.cursor is not None and (state.cursor.record_index, state.cursor.line_index) == (line.record_index, line.line_index):
        column = state.cursor.column - line.gutter_columns
        retain_window(column - 2, column + 4)
    result: list[VisibleCharacter] = []
    for column in sorted(retained):
        index, begin, finish, cells = retained[column]
        segment = segments[index]
        result.append(VisibleCharacter(
            CursorPosition(line.record_index, line.line_index, line.gutter_columns + column),
            screen_row, segment.text[begin:finish],
            None if segment.container is None else FoldIdentity(line.record_index, segment.container),
            segment.delimiter, segment.role == "match_current",
            None if segment.property is None else FoldIdentity(line.record_index, segment.property),
            segment.key_anchor, line.gutter_columns + column, cells,
        ))
    return tuple(result)


def _resolve_cursor(state: ViewState, characters: tuple[VisibleCharacter, ...]) -> CursorPosition | None:
    if not characters:
        return None
    if state.focus_property is not None and state.cursor is None:
        anchor = next((cell for cell in characters if cell.property == state.focus_property and cell.key_anchor), None)
        if anchor is not None:
            return anchor.position
    if state.focus_container is not None:
        opener = next((cell for cell in characters if cell.container == state.focus_container
                       and cell.delimiter == "open"), None)
        if opener is not None:
            return opener.position
    if state.reveal_match:
        match = next((cell for cell in characters if cell.search_focus), None)
        if match is not None:
            return match.position
    if state.cursor is None:
        return characters[0].position
    exact = next((cell for cell in characters if cell.position == state.cursor), None)
    if exact is not None:
        return exact.position
    same_record = [cell for cell in characters if cell.position.record_index == state.cursor.record_index]
    if not same_record:
        return characters[0].position
    return min(same_record, key=lambda cell: (
        abs(cell.position.line_index - state.cursor.line_index),
        abs(cell.position.column - state.cursor.column),
    )).position


def _paint_record_line(
    line: _RecordLine, *, cursor: CursorPosition | None,
    container: FoldIdentity | None, color: bool, cursor_visible: bool = True,
) -> str:
    if not color:
        return _paint(line.segments, color=False)
    result: list[str] = []
    column = 0
    for segment in line.segments:
        if segment.logical_column is not None:
            column = segment.logical_column
        for cluster in _clusters(segment.text):
            position = CursorPosition(line.record_index, line.line_index, column)
            matching = (segment.delimiter is not None and container is not None
                        and container == FoldIdentity(line.record_index, segment.container))
            focused = cursor_visible and segment.navigable and cursor == position
            style = _ANSI.get(segment.role, _ANSI["plain"])
            style += ";1" if matching else ""
            style += ";7" if matching or focused else ""
            style += ";4" if matching and focused else ""
            result.append(f"\x1b[{style}m{cluster}\x1b[0m")
            column += _text_cells(cluster)
    return "".join(result)


def _projection_footer_pieces(facts: _ProjectionFacts | _ProjectionSummary | None) -> list[str]:
    if facts is None:
        return []
    pieces: list[str] = []
    if facts.content_preview is not None:
        retained, complete = facts.content_preview
        pieces.append(f"content preview {retained}/{complete} UTF-8 bytes")
    if facts.expanded_strings or facts.skipped_expansions or facts.truncated_leaves:
        summary = (
            f"JSON display {facts.expanded_strings} expanded, "
            f"{facts.skipped_expansions} skipped, "
            f"{facts.truncated_leaves} truncated"
        )
        if facts.truncated_leaves:
            summary += (
                f" ({facts.truncated_retained_utf8_bytes}/"
                f"{facts.truncated_full_utf8_bytes} UTF-8 bytes retained)"
            )
        pieces.append(summary)
    return pieces


def _footer_lines(
    snapshot: Snapshot | None,
    state: ViewState,
    *,
    spec: ViewerSpec,
    width: int,
    width_clipped: bool,
    projection_facts: _ProjectionFacts | _ProjectionSummary | None,
    horizontal_offset: int = 0,
    max_horizontal_offset: int = 0,
) -> list[tuple[str, str]]:
    if state.prompt is not None:
        return _prompt_lines(state.prompt, width=width)
    status = ""
    role = "footer"
    if state.search is not None:
        search = state.search
        position = (
            "0/0"
            if search.current_index is None
            else f"{search.current_index + 1}/{len(search.occurrences)}"
        )
        status = (
            f"{position} occurrences • Search all text={_neutralize_text(search.query)!r} • "
            "⟦active⟧ • n/N"
        )
        projection_status = _projection_footer_pieces(projection_facts)
        if projection_status:
            status = f"{status} • {' • '.join(projection_status)}"
        if state.message:
            status = f"{state.message} • {status}"
            role = "error" if state.message_is_error else "warning"
    elif state.message:
        status = state.message
        projection_status = _projection_footer_pieces(projection_facts)
        if projection_status:
            status = f"{status} • {' • '.join(projection_status)}"
        role = "error" if state.message_is_error else "warning"
    elif snapshot is not None and snapshot.records:
        selected = snapshot.records[state.selected_index]
        hidden = 0
        if state.mode is ViewMode.SIMPLE and isinstance(selected.value, dict):
            hidden = sum(
                1 for field in selected.value if field not in spec.primary_fields
            )
        pieces = [
            f"Record {state.selected_index + 1}/{len(snapshot.records)}",
            f"source line {selected.source_line}",
        ]
        if hidden:
            pieces.append(f"{hidden} fields hidden")
        projection_status = _projection_footer_pieces(projection_facts)
        if (
            projection_facts is not None
            and projection_facts.content_preview is not None
            and projection_status
        ):
            pieces.append(projection_status.pop(0))
        if width_clipped:
            pieces.append("width clipped")
        pieces.extend(projection_status)
        status = " • ".join(pieces)
        role = "muted" if projection_status or width_clipped else "footer"
    if not status:
        status = "Immutable snapshot • no persistent viewer state"
    if horizontal_offset or max_horizontal_offset:
        cues = ("←" if horizontal_offset else "") + ("→" if horizontal_offset < max_horizontal_offset else "")
        horizontal = f"x {horizontal_offset} {cues}"
        status = f"{horizontal} • {status}"
    help_text = (
        ("←/→ pan • ? help • q close" if horizontal_offset or max_horizontal_offset else "? help • q close")
        if width < 100
        else "←/→ pan • h/j/k/l • J/K keys • Enter fold • ↑/↓ • PgUp/Dn • / search • n/N • m • ? help • q close"
    )
    return [
        (_clip_text(_neutralize_text(status), width), role),
        (_clip_text(help_text, width), "footer"),
    ]


def _prompt_lines(prompt: PromptState, *, width: int) -> list[tuple[str, str]]:
    """Keep the logical cursor visible without emitting cursor controls."""

    labels = {
        "search_query": ("Search query: ", "Query: "),
        "goto": ("Go to source line: ", "Line: "),
    }
    label = labels[prompt.kind][0 if width >= 32 else 1]
    available = max(1, width - _text_cells(label))
    before = _neutralize_text(prompt.buffer[:prompt.cursor])
    after = _neutralize_text(prompt.buffer[prompt.cursor:])
    after_budget = min(_text_cells(after), max(0, (available - 1) // 3))
    before_budget = max(0, available - 1 - after_budget)
    tail, _, clipped = _take_cells(before[::-1], before_budget)
    before = tail[::-1]
    if clipped and before_budget:
        tail, _, _ = _take_cells(before[::-1], before_budget - 1)
        before = "…" + tail[::-1]
    after_budget = max(0, available - 1 - _text_cells(before))
    after = _clip_text(after, after_budget)
    editor = label + before + "│" + after
    hint = prompt.error or "Enter submit • Esc cancel • Ctrl+U/K/W edit"
    return [
        (_clip_text(editor, width), "chrome"),
        (_clip_text(_neutralize_text(hint), width), "error" if prompt.error else "footer"),
    ]


def render_loading(
    spec: ViewerSpec,
    *,
    columns: int,
    rows: int,
    color: bool,
) -> str:
    width = min(MAX_COLUMNS, max(MIN_COLUMNS, columns))
    height = min(MAX_ROWS, max(MIN_ROWS, rows))
    state = ViewState()
    values = _header_lines(spec, state, width=width)
    values.append((_clip_text("Loading immutable JSONL snapshot…", width), "muted"))
    values.append((_clip_text("? help • q close", width), "footer"))
    return "\n".join(
        _paint((_Segment(text, role),), color=color) for text, role in values[:height]
    )


def render_frame(
    spec: ViewerSpec,
    state: ViewState,
    *,
    snapshot: Snapshot | None,
    diagnostic: InputDiagnostic | None,
    columns: int,
    rows: int,
    color: bool,
    session: RenderSession | None = None,
) -> RenderResult:
    session = session or RenderSession()
    session.bind(spec, snapshot)
    width = min(MAX_COLUMNS, max(MIN_COLUMNS, columns))
    height = min(MAX_ROWS, max(MIN_ROWS, rows))
    has_data = (not state.help_visible and diagnostic is None
                and snapshot is not None and bool(snapshot.records))
    caret_rows = int(has_data and not color)
    footer_budget = 1 if caret_rows and height == MIN_ROWS and state.prompt is None else 2
    header = _header_lines(spec, state, width=width)
    if has_data:
        header = header[:max(1, height - footer_budget - caret_rows - 1)]
    available_body_rows = max(0, height - len(header) - footer_budget - caret_rows)
    body: list[tuple[str, str] | _RecordLine | str] = []
    width_clipped = False
    selected_line_count = 0
    effective_offset = state.record_line_offset
    horizontal_offset = state.horizontal_offset
    max_horizontal_offset = 0
    body_width = width
    selected_projection_facts: _ProjectionFacts | _ProjectionSummary | None = None
    containers: list[ContainerMetadata] = []
    properties: list[PropertyMetadata] = []
    navigable_rows: list[tuple[int, tuple[int, ...]]] = []
    if state.help_visible:
        body.extend(_help_body(width)[:available_body_rows])
    else:
        status = _status_body(diagnostic, snapshot, width=width)
        if status:
            body.extend(status[:available_body_rows])
        else:
            assert snapshot is not None and snapshot.records
            gutter_width = max(1, len(str(snapshot.records[-1].source_line)))
            body_width = max(1, width - gutter_width - 5)
            remaining = available_body_rows
            for index in range(state.selected_index, len(snapshot.records)):
                record = snapshot.records[index]
                projection = session.projection(record, index, spec, state)
                logical_count = len(projection.lines)
                facts = projection.facts
                containers.extend(projection.containers)
                navigable_rows.append((index, projection.navigable_rows))
                properties.extend(PropertyMetadata(FoldIdentity(index, path), parent) for path, parent in facts.properties)
                start = 0
                if index == state.selected_index:
                    selected_line_count = logical_count
                    selected_projection_facts = facts
                    effective_offset = min(state.record_line_offset, max(0, logical_count - 1))
                    focus_row = projection.match_row
                    if (state.focus_property is not None and state.cursor is None
                            and state.focus_property.record_index == index):
                        focus_row = projection.key_rows.get(state.focus_property.path, focus_row)
                    if state.focus_container is not None and state.focus_container.record_index == index:
                        focus_row = projection.container_rows.get(state.focus_container.path, focus_row)
                    if (state.reveal_match or state.focus_container is not None or (state.focus_property is not None and state.cursor is None)) and focus_row is not None:
                        if not effective_offset <= focus_row < effective_offset + max(1, remaining):
                            effective_offset = max(0, focus_row - max(0, remaining // 2))
                    start = effective_offset
                take = min(remaining, max(0, logical_count - start))
                body.extend(_record_line(record, index, row, projection, state, gutter_width, session)
                            for row in range(start, start + take))
                remaining -= take
                if remaining <= 0:
                    break
                if index + 1 < len(snapshot.records):
                    body.append("")
                    remaining -= 1
                    if remaining <= 0:
                        break
    logical_characters = tuple(
        cell for row, line in enumerate(body, start=len(header))
        if isinstance(line, _RecordLine) for cell in _logical_characters(line, row, state, body_width)
    )
    projected_width = max((line.body_cells for line in body if isinstance(line, _RecordLine)), default=0)
    max_horizontal_offset = max(0, projected_width - body_width)
    if has_data:
        horizontal_offset = min(max(0, horizontal_offset), max_horizontal_offset)
    projected_body = tuple(body)
    focus_line: tuple[int, int] | None = None
    focus_prefix = False
    first_focus_cells = 0
    target = _resolve_cursor(state, logical_characters)
    target_cell = next((cell for cell in logical_characters if cell.position == target), None)
    cursor_line: tuple[int, int] | None = None
    cursor_span: tuple[int, int] | None = None
    if target_cell is not None:
        target_row = next(line for line in projected_body if isinstance(line, _RecordLine)
                          and line.record_index == target.record_index and line.line_index == target.line_index)
        start = target.column - target_row.gutter_columns
        end = start + _text_cells(target_cell.text)
        cursor_line = (target.record_index, target.line_index)
        cursor_span = (start, end)
        if state.reveal_cursor or state.reveal_match or state.focus_container is not None or state.cursor is None:
            available = body_width
            if state.reveal_match and target_cell.search_focus:
                # Give the keyword priority over context, brackets, and ellipsis.
                # The footer already reports overflow when all body cells are needed.
                focus_line = (target.record_index, target.line_index)
                column = 0
                match_started = False
                for segment, part in zip(target_row.segments[4:], target_row.layout.parts):
                    cells = part.cells
                    if segment.role == "match_current":
                        if not match_started:
                            start = column
                            match_started = True
                        end = column + cells
                    elif match_started:
                        break
                    column += cells
                if end - start + 2 <= body_width:
                    start, end = start - 1, end + 1
                elif end - start > body_width:
                    first_focus_cells = _text_cells(target_cell.text)
                    focus_prefix = body_width >= first_focus_cells + 2
                    if focus_prefix:
                        start -= 1
                    end = min(end, start + body_width)
                    horizontal_offset = start
                available = body_width
            if start < horizontal_offset:
                horizontal_offset = start
            elif end > horizontal_offset + available:
                horizontal_offset = max(0, end - available)
            horizontal_offset = min(max_horizontal_offset, horizontal_offset)
    body = [(_focus_prefix_window(line, horizontal_offset, body_width, first_focus_cells)
             if focus_prefix and (line.record_index, line.line_index) == focus_line
             else _window_line(line, horizontal_offset, body_width,
                               overflow_cue=(line.record_index, line.line_index) != focus_line,
                               focus_span=cursor_span if (line.record_index, line.line_index) == cursor_line else None))
            if isinstance(line, _RecordLine) else line for line in body]
    width_clipped = bool(horizontal_offset or max_horizontal_offset)
    characters = tuple(
        cell for row, line in enumerate(body, start=len(header))
        if isinstance(line, _RecordLine) for cell in _visible_characters(line, row)
    )
    cursor = _resolve_cursor(state, characters)
    focused = next((cell for cell in characters if cell.position == cursor), None)
    navigation_state = replace(state, cursor=cursor, horizontal_offset=horizontal_offset)
    logical_characters = tuple(
        cell for row, line in enumerate(projected_body, start=len(header))
        if isinstance(line, _RecordLine)
        for cell in _logical_characters(line, row, navigation_state, body_width)
    )
    if caret_rows and focused is not None:
        characters = tuple(replace(cell, screen_row=cell.screen_row + int(cell.screen_row > focused.screen_row))
                           for cell in characters)
    footer = _footer_lines(
        snapshot, state, spec=spec, width=width, width_clipped=width_clipped,
        projection_facts=selected_projection_facts,
        horizontal_offset=horizontal_offset if has_data else 0,
        max_horizontal_offset=max_horizontal_offset,
    )[:footer_budget]
    rendered_body: list[str] = []
    idle_body: list[str] = []
    for row, line in enumerate(body, start=len(header)):
        if isinstance(line, _RecordLine):
            idle_body.append(_paint_record_line(
                line, cursor=cursor, container=None if focused is None else focused.container,
                color=color, cursor_visible=False,
            ))
            rendered_body.append(_paint_record_line(
                line, cursor=cursor, container=None if focused is None else focused.container, color=color,
            ))
        elif isinstance(line, str):
            rendered_body.append(line)
        else:
            rendered_body.append(_paint((_Segment(line[0], line[1]),), color=color))
        if not isinstance(line, _RecordLine):
            idle_body.append(rendered_body[-1])
        if caret_rows and focused is not None and row == focused.screen_row:
            idle_body.append("")
            rendered_body.append(" " * focused.screen_column + "^")
    rendered_header = [_paint((_Segment(text, role),), color=color) for text, role in header]
    rendered_footer = [_paint((_Segment(text, role),), color=color) for text, role in footer]
    frame_lines = (rendered_header + rendered_body + rendered_footer)[:height]
    return RenderResult(
        text="\n".join(frame_lines), body_rows=max(1, available_body_rows),
        selected_line_count=selected_line_count, record_line_offset=effective_offset,
        cursor=cursor, characters=characters, containers=tuple(containers),
        navigable_rows=tuple(navigable_rows), logical_characters=logical_characters,
        horizontal_offset=horizontal_offset, max_horizontal_offset=max_horizontal_offset, body_width=body_width,
        columns=width, rows=height, color=color,
        properties=tuple(properties),
        idle_text="\n".join((rendered_header + idle_body + rendered_footer)[:height]),
    )
