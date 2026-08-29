"""Transient viewer state machine over an injected host boundary."""

from __future__ import annotations

import json
from dataclasses import replace

from ._input import InputFailure, parse_jsonl
from ._model import SearchState, Snapshot, ViewMode, ViewState
from ._render import render_frame, render_loading
from .contracts import ViewerHost, ViewerSpec


_MAX_EVENT_CHARACTERS = 8_192
_MAX_QUERY_CHARACTERS = 1_024


def _search_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _find_matches(snapshot: Snapshot, field: str, query: str) -> tuple[int, ...]:
    folded = query.casefold()
    matches: list[int] = []
    for index, record in enumerate(snapshot.records):
        if not isinstance(record.value, dict) or field not in record.value:
            continue
        if folded in _search_text(record.value[field]).casefold():
            matches.append(index)
    return tuple(matches)


def _message(state: ViewState, text: str, *, error: bool = False) -> ViewState:
    return replace(state, message=text, message_is_error=error)


def _clear_message(state: ViewState) -> ViewState:
    return replace(state, message=None, message_is_error=False)


def _transition(
    state: ViewState,
    event: object,
    snapshot: Snapshot,
    spec: ViewerSpec,
    *,
    page_size: int,
    selected_line_count: int,
) -> tuple[ViewState, bool]:
    if event is None or event == "close":
        return state, True
    if type(event) is not str or len(event) > _MAX_EVENT_CHARACTERS:
        return _message(
            state, "Unsupported host event; press h for help.", error=True
        ), False

    if state.help_visible:
        if event in {"help", "cancel"}:
            return replace(state, help_visible=False, message=None), False
        if event == "close":
            return state, True
        return state, False

    if event == "help":
        return replace(state, help_visible=True, message=None), False
    if event == "cancel":
        if state.search is not None:
            return replace(state, search=None, message="Search cleared."), False
        if state.message is not None:
            return _clear_message(state), False
        return state, True
    if event == "clear_search":
        return replace(state, search=None, message="Search cleared."), False

    if not snapshot.records:
        return _message(state, "The immutable snapshot has no records."), False

    if event in {"up", "down"}:
        direction = -1 if event == "up" else 1
        selected = min(
            len(snapshot.records) - 1,
            max(0, state.selected_index + direction),
        )
        return replace(
            state,
            selected_index=selected,
            record_line_offset=0,
            message=None,
        ), False

    if event in {"page_up", "page_down"}:
        step = max(1, page_size - 1)
        if event == "page_up":
            if state.record_line_offset > 0:
                offset = max(0, state.record_line_offset - step)
                return replace(state, record_line_offset=offset, message=None), False
            if state.selected_index > 0:
                return replace(
                    state,
                    selected_index=state.selected_index - 1,
                    record_line_offset=0,
                    message=None,
                ), False
            return _clear_message(state), False
        if state.record_line_offset + step < selected_line_count:
            return replace(
                state,
                record_line_offset=state.record_line_offset + step,
                message=None,
            ), False
        if state.selected_index + 1 < len(snapshot.records):
            return replace(
                state,
                selected_index=state.selected_index + 1,
                record_line_offset=0,
                message=None,
            ), False
        return _clear_message(state), False

    if event == "toggle_mode":
        if (
            state.mode is ViewMode.VERBOSE
            and state.search is not None
            and state.search.matches
            and state.search.field not in spec.primary_fields
        ):
            return _message(
                state,
                "Verbose mode is required for the selected hidden-field match.",
            ), False
        mode = ViewMode.VERBOSE if state.mode is ViewMode.SIMPLE else ViewMode.SIMPLE
        return replace(
            state,
            mode=mode,
            record_line_offset=0,
            message=f"Switched to {mode.value} mode.",
        ), False

    if event in {"next_match", "previous_match"}:
        search = state.search
        if search is None or not search.matches:
            return _message(state, "No search matches are active."), False
        assert search.current_index is not None
        delta = 1 if event == "next_match" else -1
        current = (search.current_index + delta) % len(search.matches)
        selected = search.matches[current]
        return replace(
            state,
            selected_index=selected,
            record_line_offset=0,
            search=replace(search, current_index=current),
            message=None,
        ), False

    if event.startswith("goto\t"):
        raw_line = event.partition("\t")[2]
        try:
            source_line = int(raw_line, 10)
        except ValueError:
            return _message(
                state, "Go-to line must be a positive integer.", error=True
            ), False
        if source_line <= 0:
            return _message(
                state, "Go-to line must be a positive integer.", error=True
            ), False
        for index, record in enumerate(snapshot.records):
            if record.source_line == source_line:
                return replace(
                    state,
                    selected_index=index,
                    record_line_offset=0,
                    message=f"Moved to source line {source_line}.",
                ), False
        return _message(
            state, f"Source line {source_line} is not a JSONL record."
        ), False

    if event.startswith("search\t"):
        parts = event.split("\t", 2)
        if len(parts) != 3:
            return _message(
                state, "Search requires a field and query.", error=True
            ), False
        _, field, query = parts
        if field not in spec.searchable_fields:
            allowed = ", ".join(spec.searchable_fields) or "(none)"
            return _message(
                state,
                f"Field is not searchable. Allowed fields: {allowed}.",
                error=True,
            ), False
        if not query:
            return _message(state, "Search query must not be empty.", error=True), False
        if len(query) > _MAX_QUERY_CHARACTERS:
            return _message(state, "Search query is too long.", error=True), False
        matches = _find_matches(snapshot, field, query)
        search = SearchState(
            field=field,
            query=query,
            matches=matches,
            current_index=0 if matches else None,
        )
        if not matches:
            return replace(
                state,
                search=search,
                message=f"No matches in {field}.",
                message_is_error=False,
            ), False
        mode = state.mode
        message = None
        if field not in spec.primary_fields and mode is ViewMode.SIMPLE:
            mode = ViewMode.VERBOSE
            message = "Hidden-field match selected; switched to Verbose mode."
        return replace(
            state,
            selected_index=matches[0],
            record_line_offset=0,
            mode=mode,
            search=search,
            message=message,
            message_is_error=False,
        ), False

    return _message(state, "Unknown event; press h for help.", error=True), False


def view_jsonl(source: bytes, spec: ViewerSpec, host: ViewerHost) -> None:
    """View one immutable JSONL byte snapshot through an injected host.

    All navigation, mode, search, help, and message state is local to this call.
    The source is never written and no state is returned or persisted.
    """

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if not isinstance(spec, ViewerSpec):
        raise TypeError("spec must be a ViewerSpec")

    closed = False
    try:
        columns, rows = host.terminal_size()
        color = bool(host.color_enabled())
        host.present(
            render_loading(
                spec,
                columns=columns,
                rows=rows,
                color=color,
            )
        )
        diagnostic = None
        try:
            snapshot = parse_jsonl(source)
        except InputFailure as exc:
            snapshot = Snapshot((), len(source))
            diagnostic = exc.diagnostic

        state = ViewState()
        while not closed:
            columns, rows = host.terminal_size()
            rendered = render_frame(
                spec,
                state,
                snapshot=snapshot,
                diagnostic=diagnostic,
                columns=columns,
                rows=rows,
                color=bool(host.color_enabled()),
            )
            host.present(rendered.text)
            event = host.read_event()
            if diagnostic is not None and event not in {
                "help",
                "cancel",
                "close",
                None,
            }:
                state = _message(
                    state, "Malformed input is read-only; press q to close."
                )
                continue
            state, closed = _transition(
                state,
                event,
                snapshot,
                spec,
                page_size=rendered.body_rows,
                selected_line_count=rendered.selected_line_count,
            )
    finally:
        host.close_view()
