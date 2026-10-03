"""Deterministic semantic ANSI/plain rendering for transient view state."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable

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


def _character_cells(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 2
    return 1


def _text_cells(value: str) -> int:
    return sum(_character_cells(character) for character in value)


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
        ("h/l         move cursor left/right; wrap visible rows", "plain"),
        ("j/k         move cursor down/up within visible JSON", "plain"),
        ("J/K next/previous sibling property key", "plain"),
        ("Cursor blinks every 500 ms while idle", "plain"),
        ("↑/↓         previous/next source record", "plain"),
        ("Enter/fold  collapse/expand container: {...} / [...]", "plain"),
        ("? / help    show or dismiss help", "plain"),
        ("PgUp/PgDn   scroll one record, then cross record boundaries", "plain"),
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


def _reveal_segments(segments: tuple[_Segment, ...], width: int, *, key: bool = False) -> tuple[_Segment, ...]:
    """Keep the active character visible even beyond indentation or long keys."""
    focus = next((i for i, segment in enumerate(segments)
                  if (segment.key_anchor if key else segment.role == "match_current")), None)
    if focus is None:
        return segments
    if width < 24:
        # Tiny rows spend their cells on the active token, not context or a
        # trailing clipping ellipsis. The compact gutter below ensures room
        # for both brackets and even a ten-cell neutralized Unicode escape.
        token, _, _ = _take_cells(segments[focus].text, max(0, width - 2))
        return (
            _Segment("⟦", "current"),
            replace(segments[focus], text=token),
            _Segment("⟧", "current"),
        )
    before = segments[:focus]
    # Reserve most of the row for the focused occurrence, including tiny views.
    budget = max(2, width // 4)
    if sum(_text_cells(segment.text) for segment in before) <= budget:
        return segments
    tail: list[_Segment] = []
    remaining = max(0, budget - 1)
    for segment in reversed(before):
        piece, used, _ = _take_cells(segment.text[::-1], remaining)
        if piece:
            tail.append(replace(segment, text=piece[::-1]))
        remaining -= used
        if remaining <= 0:
            break
    return (_Segment("…", "muted"),) + tuple(reversed(tail)) + segments[focus:]


@dataclass(frozen=True, slots=True)
class _RecordLine:
    segments: tuple[_Segment, ...]
    record_index: int
    line_index: int


def _record_lines(
    record: Record, index: int, spec: ViewerSpec, state: ViewState, *,
    gutter_width: int, body_width: int, color: bool,
) -> tuple[list[_RecordLine], bool, int, _ProjectionFacts, int | None, tuple[ContainerMetadata, ...]]:
    active = state.search.current_occurrence if state.search is not None else None
    if active is not None and active.record_index != index:
        active = None
    folds = frozenset(fold.path for fold in state.folds if fold.record_index == index)
    logical, facts = _format_record(record, spec, state.mode, active, folds)
    visible_properties = {segment.property for line in logical for segment in line.segments if segment.key_anchor}
    facts.properties[:] = [(path, parent) for path, parent in facts.properties if path in visible_properties]
    matched = state.search is not None and state.search.has_record(index)
    marker, marker_role = _record_marker(index, state, matched=matched)
    result: list[_RecordLine] = []
    containers: list[ContainerMetadata] = []
    any_width_clip = False
    focus_row = None
    for row, line in enumerate(logical):
        for segment in line.segments:
            if segment.delimiter == "open":
                assert segment.container is not None
                containers.append(ContainerMetadata(
                    FoldIdentity(index, segment.container), segment.nonempty, segment.folded,
                ))
        key_focused = state.focus_property is not None and any(
            segment.key_anchor and FoldIdentity(index, segment.property) == state.focus_property
            for segment in line.segments)
        focused = key_focused or any(segment.role == "match_current" for segment in line.segments)
        if focused:
            focus_row = row
        gutter = (
            _Segment(marker, marker_role), _Segment(" "),
            _Segment(str(record.source_line).rjust(gutter_width), "gutter"),
            _Segment(" │ ", "gutter"),
        )
        line_width = body_width
        if focused and body_width < 12:
            gutter = ()
            line_width += gutter_width + 5
        value_segments = _reveal_segments(line.segments, line_width, key=key_focused)
        opener = next((i for i, segment in enumerate(line.segments)
                       if segment.delimiter == "open" and segment.nonempty), None)
        if not focused and opener is not None and sum(
                _text_cells(segment.text) for segment in line.segments[:opener + 1]) > line_width:
            # Retain a clipped key/cue prefix and the structural token. Otherwise
            # folding a visible child behind a long key could hide its opener
            # again on the next frame, leaving no stable cursor cell.
            suffix = line.segments[opener:]
            suffix_width = sum(_text_cells(segment.text) for segment in suffix)
            prefix, _ = _clip_segments(line.segments[:opener], max(0, line_width - suffix_width))
            value_segments = prefix + suffix
        clipped, width_clip = _clip_segments(value_segments, line_width)
        any_width_clip = any_width_clip or width_clip or value_segments != line.segments
        result.append(_RecordLine(gutter + clipped, index, row))
    return result, any_width_clip, len(logical), facts, focus_row, tuple(containers)


def _clusters(text: str) -> Iterable[str]:
    """Keep combining marks attached to their readable inverse-video cell."""
    cluster = ""
    for character in text:
        if _character_cells(character) == 0 and cluster:
            cluster += character
        else:
            if cluster:
                yield cluster
            cluster = character
    if cluster:
        yield cluster


def _visible_characters(line: _RecordLine, screen_row: int) -> tuple[VisibleCharacter, ...]:
    result: list[VisibleCharacter] = []
    column = 0
    for segment in line.segments:
        for cluster in _clusters(segment.text):
            width = _text_cells(cluster)
            if segment.navigable and width:
                result.append(VisibleCharacter(
                    CursorPosition(line.record_index, line.line_index, column),
                    screen_row, cluster,
                    None if segment.container is None else FoldIdentity(line.record_index, segment.container),
                    segment.delimiter, segment.role == "match_current",
                    None if segment.property is None else FoldIdentity(line.record_index, segment.property),
                    segment.key_anchor,
                ))
            column += width
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


def _projection_footer_pieces(facts: _ProjectionFacts | None) -> list[str]:
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
    projection_facts: _ProjectionFacts | None,
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
    help_text = (
        "? help • q close"
        if width < 91
        else "h/j/k/l • J/K siblings • Enter fold • ↑/↓ • PgUp/Dn • / search • n/N • m • ? help • q close"
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
) -> RenderResult:
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
    selected_projection_facts: _ProjectionFacts | None = None
    containers: list[ContainerMetadata] = []
    properties: list[PropertyMetadata] = []
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
                record_lines, clipped, logical_count, facts, focus_row, record_containers = _record_lines(
                    snapshot.records[index], index, spec, state,
                    gutter_width=gutter_width, body_width=body_width, color=color,
                )
                containers.extend(record_containers)
                properties.extend(PropertyMetadata(FoldIdentity(index, path), parent)
                                  for path, parent in facts.properties)
                width_clipped = width_clipped or clipped
                if index == state.selected_index:
                    selected_line_count = logical_count
                    selected_projection_facts = facts
                    effective_offset = min(state.record_line_offset, max(0, logical_count - 1))
                    if state.focus_property is not None and state.cursor is None:
                        focus_row = next((line.line_index for line in record_lines if any(
                            segment.key_anchor and FoldIdentity(index, segment.property) == state.focus_property
                            for segment in line.segments)), focus_row)
                    if state.focus_container is not None:
                        opener_row = next((line.line_index for line in record_lines
                            if any(segment.delimiter == "open"
                                   and FoldIdentity(index, segment.container) == state.focus_container
                                   for segment in line.segments)), None)
                        if opener_row is not None:
                            focus_row = opener_row
                    if (state.reveal_match or state.focus_container is not None or (state.focus_property is not None and state.cursor is None)) and focus_row is not None:
                        if not effective_offset <= focus_row < effective_offset + max(1, remaining):
                            effective_offset = max(0, focus_row - max(0, remaining // 2))
                    record_lines = record_lines[effective_offset:]
                if not record_lines:
                    continue
                take = min(remaining, len(record_lines))
                body.extend(record_lines[:take])
                remaining -= take
                if remaining <= 0:
                    break
                if index + 1 < len(snapshot.records):
                    body.append("")
                    remaining -= 1
                    if remaining <= 0:
                        break
    characters = tuple(
        cell for row, line in enumerate(body, start=len(header))
        if isinstance(line, _RecordLine) for cell in _visible_characters(line, row)
    )
    cursor = _resolve_cursor(state, characters)
    focused = next((cell for cell in characters if cell.position == cursor), None)
    if caret_rows and focused is not None:
        characters = tuple(replace(cell, screen_row=cell.screen_row + int(cell.screen_row > focused.screen_row))
                           for cell in characters)
    footer = _footer_lines(
        snapshot, state, spec=spec, width=width, width_clipped=width_clipped,
        projection_facts=selected_projection_facts,
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
            rendered_body.append(" " * focused.position.column + "^")
    rendered_header = [_paint((_Segment(text, role),), color=color) for text, role in header]
    rendered_footer = [_paint((_Segment(text, role),), color=color) for text, role in footer]
    frame_lines = (rendered_header + rendered_body + rendered_footer)[:height]
    return RenderResult(
        text="\n".join(frame_lines), body_rows=max(1, available_body_rows),
        selected_line_count=selected_line_count, record_line_offset=effective_offset,
        cursor=cursor, characters=characters, containers=tuple(containers),
        columns=width, rows=height, color=color,
        properties=tuple(properties),
        idle_text="\n".join((rendered_header + idle_body + rendered_footer)[:height]),
    )
