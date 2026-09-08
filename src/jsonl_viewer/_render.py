"""Deterministic semantic ANSI/plain rendering for transient view state."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from ._json import Expansion, ExpansionBudget, SkippedExpansion, expand_json_string
from ._model import (
    InputDiagnostic,
    JSONPath,
    JSONValue,
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


@dataclass(frozen=True, slots=True)
class _DisplayLine:
    segments: tuple[_Segment, ...]
    path: JSONPath


@dataclass(slots=True)
class _ProjectionFacts:
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
            result.append(_Segment(piece, segment.role))
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
        _Segment(token[:-1] + '…"', "string"),
    )


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
) -> list[_DisplayLine]:
    leading = (_Segment(" " * indent),) + prefix
    if isinstance(value, dict):
        if not value:
            return [_DisplayLine(leading + (_Segment("{}"),), path)]
        lines = [_DisplayLine(leading + (_Segment("{"),), path)]
        items = tuple(value.items())
        for index, (key, child) in enumerate(items):
            child_prefix = (
                _Segment(_safe_json_token(key), "key"),
                _Segment(": "),
            )
            child_lines = _format_value(
                child,
                indent=indent + 2,
                display_depth=display_depth + 1,
                path=path + (key,),
                prefix=child_prefix,
                string_limit=string_limit,
                content_field=content_field,
                budget=budget,
                facts=facts,
            )
            if index + 1 < len(items):
                last = child_lines[-1]
                child_lines[-1] = _DisplayLine(
                    last.segments + (_Segment(","),),
                    last.path,
                )
            lines.extend(child_lines)
        lines.append(_DisplayLine((_Segment(" " * indent + "}"),), path))
        return lines
    if isinstance(value, list):
        if not value:
            return [_DisplayLine(leading + (_Segment("[]"),), path)]
        lines = [_DisplayLine(leading + (_Segment("["),), path)]
        for index, child in enumerate(value):
            child_lines = _format_value(
                child,
                indent=indent + 2,
                display_depth=display_depth + 1,
                path=path + (index,),
                string_limit=string_limit,
                content_field=content_field,
                budget=budget,
                facts=facts,
            )
            if index + 1 < len(value):
                last = child_lines[-1]
                child_lines[-1] = _DisplayLine(
                    last.segments + (_Segment(","),),
                    last.path,
                )
            lines.extend(child_lines)
        lines.append(_DisplayLine((_Segment(" " * indent + "]"),), path))
        return lines
    if isinstance(value, str):
        expansion = expand_json_string(
            value,
            display_depth=display_depth,
            budget=budget,
        )
        if isinstance(expansion, Expansion):
            facts.expanded_strings += 1
            cue = _Segment(
                f"[expanded JSON string ×{expansion.layers}] ",
                "muted",
            )
            return _format_value(
                expansion.value,
                indent=indent,
                display_depth=display_depth,
                path=path,
                prefix=prefix + (cue,),
                string_limit=string_limit,
                content_field=content_field,
                budget=budget,
                facts=facts,
            )
        if isinstance(expansion, SkippedExpansion):
            facts.skipped_expansions += 1
            leading += (
                _Segment(
                    f"[JSON expansion skipped: {expansion.reason}] ",
                    "warning",
                ),
            )
        token = _string_segments(
            value,
            maximum=string_limit,
            path=path,
            content_field=content_field,
            facts=facts,
        )
    elif value is None:
        token = (_Segment("null", "literal"),)
    elif type(value) is bool:
        token = (_Segment("true" if value else "false", "literal"),)
    else:
        token = (_Segment(json.dumps(value, allow_nan=False), "number"),)
    return [_DisplayLine(leading + token, path)]


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
    )
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
        ("↑/↓ or j/k  previous/next source record", "plain"),
        ("PgUp/PgDn   scroll one record, then cross record boundaries", "plain"),
        ("g LINE      go to an exact source-record line", "plain"),
        ("/ FIELD QUERY  search any field or nested path", "plain"),
        ("// QUERY    search all text; leave the key-mode field blank", "plain"),
        ("n / N       next / previous search match", "plain"),
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
                    "Source bytes are unchanged. Press h for help or q to close.", width
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
        return "@", "match_current"
    if matched:
        return "*", "match_other"
    if index == state.selected_index:
        return ">", "current"
    return " ", "gutter"


def _is_match_line(
    index: int,
    line: _DisplayLine,
    state: ViewState,
    match_paths: frozenset[JSONPath],
) -> str | None:
    search = state.search
    if search is None or not any(
        line.path[:length] in match_paths for length in range(len(line.path) + 1)
    ):
        return None
    if search.current_record_index == index:
        return "match_current"
    return "match_other"


def _record_lines(
    record: Record,
    index: int,
    spec: ViewerSpec,
    state: ViewState,
    *,
    gutter_width: int,
    body_width: int,
    color: bool,
) -> tuple[list[str], bool, int, _ProjectionFacts]:
    logical, facts = _format_record(record, spec, state.mode)
    match_paths = frozenset(
        state.search.paths_for_record(index) if state.search is not None else ()
    )
    marker, marker_role = _record_marker(index, state, matched=bool(match_paths))
    result: list[str] = []
    any_width_clip = False
    for line in logical:
        match_role = _is_match_line(index, line, state, match_paths)
        value_segments = line.segments
        if match_role is not None:
            value_segments = tuple(
                _Segment(segment.text, match_role) if segment.text.strip() else segment
                for segment in value_segments
            )
        clipped, width_clip = _clip_segments(value_segments, body_width)
        any_width_clip = any_width_clip or width_clip
        gutter = (
            _Segment(marker, marker_role),
            _Segment(" "),
            _Segment(str(record.source_line).rjust(gutter_width), "gutter"),
            _Segment(" │ ", "gutter"),
        )
        result.append(_paint(gutter + clipped, color=color))
    return result, any_width_clip, len(logical), facts


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
            else f"{search.current_index + 1}/{len(search.matches)}"
        )
        status = (
            f"Search {search.field or 'all text'}={_neutralize_text(search.query)!r} • "
            f"{position} • @ current, * other"
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
        "h help • q close"
        if width < 48
        else "↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close"
    )
    return [
        (_clip_text(_neutralize_text(status), width), role),
        (_clip_text(help_text, width), "footer"),
    ]


def _prompt_lines(prompt: PromptState, *, width: int) -> list[tuple[str, str]]:
    """Keep the logical cursor visible without emitting cursor controls."""

    labels = {
        "search_field": ("Search field: ", "Field: "),
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
    hint = prompt.error or (
        "Enter blank for all text • field/path • Esc cancel"
        if prompt.kind == "search_field"
        else "Enter submit • Esc cancel • Ctrl+U/K/W edit"
    )
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
    values.append((_clip_text("h help • q close", width), "footer"))
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
    header = _header_lines(spec, state, width=width)
    footer_budget = 2
    available_body_rows = max(0, height - len(header) - footer_budget)
    body: list[tuple[str, str] | str] = []
    width_clipped = False
    selected_line_count = 0
    selected_projection_facts: _ProjectionFacts | None = None

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
                record_lines, clipped, logical_count, projection_facts = _record_lines(
                    snapshot.records[index],
                    index,
                    spec,
                    state,
                    gutter_width=gutter_width,
                    body_width=body_width,
                    color=color,
                )
                width_clipped = width_clipped or clipped
                if index == state.selected_index:
                    selected_line_count = logical_count
                    selected_projection_facts = projection_facts
                    record_lines = record_lines[state.record_line_offset :]
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

    footer = _footer_lines(
        snapshot,
        state,
        spec=spec,
        width=width,
        width_clipped=width_clipped,
        projection_facts=selected_projection_facts,
    )
    rendered_header = [
        _paint((_Segment(text, role),), color=color) for text, role in header
    ]
    rendered_body = [
        value
        if isinstance(value, str)
        else _paint((_Segment(value[0], value[1]),), color=color)
        for value in body
    ]
    rendered_footer = [
        _paint((_Segment(text, role),), color=color) for text, role in footer
    ]
    frame_lines = (rendered_header + rendered_body + rendered_footer)[:height]
    return RenderResult(
        text="\n".join(frame_lines),
        body_rows=max(1, available_body_rows),
        selected_line_count=selected_line_count,
    )
