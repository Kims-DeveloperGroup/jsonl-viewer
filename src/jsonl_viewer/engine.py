"""Transient viewer state machine over an injected host boundary."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from ._input import InputFailure, parse_jsonl
from ._model import FoldIdentity, PromptState, RenderResult, SearchState, Snapshot, ViewMode, ViewState
from ._render import render_frame, render_loading
from ._search import SearchLimitError, find_matches
from .contracts import ViewerHost, ViewerSpec


_MAX_EVENT_CHARACTERS = 8_192
_MAX_QUERY_CHARACTERS = 1_024
_MAX_GOTO_CHARACTERS = 128


def _input_error(state: ViewState, message: str) -> ViewState:
    if state.prompt is not None:
        return replace(state, prompt=replace(state.prompt, error=message))
    return _message(state, message, error=True)


def _safe_input(value: str, *, allow_tab: bool = False) -> bool:
    return all(
        (allow_tab and character == "\t")
        or (
            unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
            and character not in {"\u2028", "\u2029"}
        )
        for character in value
    )


def _line_action(value: str) -> str:
    """Interpret literal ordinary-line transport inside the viewer boundary."""

    command = value.strip()
    mapping = {
        "q": "close", "quit": "close", "j": "cursor_down", "down": "down",
        "k": "cursor_up", "up": "up", "pgdn": "page_down", "pgup": "page_up",
        "n": "next_match", "N": "previous_match", "m": "toggle_mode",
        "h": "cursor_left", "l": "cursor_right", "J": "next_sibling", "K": "previous_sibling", "?": "help", "help": "help", "fold": "toggle_fold", "esc": "cancel",
        "c": "clear_search", "clear": "clear_search",
    }
    if command in mapping:
        return mapping[command]
    if command.startswith("g "):
        line = command[2:].strip()
        if line and "\t" not in line:
            return "goto\t" + line
    if command == "//" or command.startswith("// "):
        return "search\t" + command[2:].strip()
    if command == "/" or command.startswith("/ "):
        return "search\t" + command[1:].strip()
    return "unknown"


def _edit_prompt(prompt: PromptState, key: str) -> PromptState:
    """Edit code points without sharing terminal or application line state."""

    buffer, cursor = prompt.buffer, prompt.cursor
    if key in {"home", "ctrl_a"}:
        cursor = 0
    elif key in {"end", "ctrl_e"}:
        cursor = len(buffer)
    elif key == "left":
        cursor = max(0, cursor - 1)
    elif key == "right":
        cursor = min(len(buffer), cursor + 1)
    elif key == "backspace" and cursor:
        buffer = buffer[:cursor - 1] + buffer[cursor:]
        cursor -= 1
    elif key == "delete":
        buffer = buffer[:cursor] + buffer[cursor + 1:]
    elif key == "ctrl_u":
        buffer, cursor = buffer[cursor:], 0
    elif key == "ctrl_k":
        buffer = buffer[:cursor]
    elif key == "ctrl_w":
        start = cursor
        while start and buffer[start - 1].isspace():
            start -= 1
        while start and not buffer[start - 1].isspace():
            start -= 1
        buffer, cursor = buffer[:start] + buffer[cursor:], start
    else:
        return prompt
    return replace(prompt, buffer=buffer, cursor=cursor, error=None)


def _insert_prompt(state: ViewState, text: str) -> ViewState:
    assert state.prompt is not None
    prompt = state.prompt
    limit = {
        "search_query": _MAX_QUERY_CHARACTERS,
        "goto": _MAX_GOTO_CHARACTERS,
    }[prompt.kind]
    if len(prompt.buffer) + len(text) > limit:
        return _input_error(state, f"Input is limited to {limit} characters.")
    buffer = prompt.buffer[:prompt.cursor] + text + prompt.buffer[prompt.cursor:]
    return replace(
        state,
        prompt=replace(prompt, buffer=buffer, cursor=prompt.cursor + len(text), error=None),
    )


def _submit_prompt(state: ViewState, snapshot: Snapshot) -> tuple[ViewState, str | None]:
    assert state.prompt is not None
    prompt = state.prompt
    value = prompt.buffer.strip()
    if prompt.kind == "search_query":
        if not value:
            return _input_error(state, "Search query must not be empty."), None
        return replace(state, prompt=None), f"search\t{value}"
    try:
        valid = int(value, 10) > 0
    except ValueError:
        valid = False
    if not valid:
        return _input_error(state, "Go-to line must be a positive integer."), None
    return replace(state, prompt=None), "goto\t" + value


def _has_hidden_match(
    search: SearchState,
    record_index: int,
    snapshot: Snapshot,
    spec: ViewerSpec,
) -> bool:
    value = snapshot.records[record_index].value
    if not isinstance(value, dict):
        return False
    hidden = set(value).difference(spec.primary_fields)
    hit = search.current_occurrence
    return bool(hidden) and hit is not None and (
        not hit.path or hit.path[0] in hidden
    )


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
    malformed: bool = False,
    rendered: RenderResult | None = None,
) -> tuple[ViewState, bool]:
    """Validate transport before applying context-sensitive viewer behavior."""

    rendered_state = state

    def projection_key(current: ViewState) -> tuple:
        return (current.selected_index, current.record_line_offset, current.mode,
                current.search, current.folds, current.help_visible, current.focus_container, current.focus_property)

    def action(current: ViewState, value: str | None) -> tuple[ViewState, bool]:
        nonlocal rendered, rendered_state
        if (rendered is not None and value in {
                "cursor_left", "cursor_right", "cursor_up", "cursor_down", "toggle_fold",
                "page_up", "page_down", "next_sibling", "previous_sibling",
            } and projection_key(current) != projection_key(rendered_state)):
            # A text envelope may combine mode/fold/navigation and movement.
            # Refresh its structural positions before consuming another cursor key.
            rendered = render_frame(spec, current, snapshot=snapshot, diagnostic=None,
                                    columns=rendered.columns, rows=rendered.rows, color=rendered.color)
            current = replace(current, cursor=rendered.cursor if rendered.characters else current.cursor,
                              record_line_offset=rendered.record_line_offset, focus_container=None)
            rendered_state = current
        return _semantic_transition(
            current, value, snapshot, spec,
            page_size=rendered.body_rows if rendered is not None else page_size,
            selected_line_count=rendered.selected_line_count if rendered is not None else selected_line_count,
            malformed=malformed, rendered=rendered,
        )

    if event is None:
        return action(state, None)
    if type(event) is not str:
        return _input_error(state, "Unsupported host event; press ? for help."), False
    if len(event) > _MAX_EVENT_CHARACTERS + len("text\t"):
        return _input_error(state, "Unsupported host event; press ? for help."), False
    kind, separator, payload = event.partition("\t")
    if spec.input_protocol == "keys" and separator and kind in {"text", "key", "line"}:
        if len(payload) > _MAX_EVENT_CHARACTERS:
            return _input_error(state, "Host input is too long."), False
        if kind == "line":
            if not _safe_input(payload, allow_tab=True):
                return _input_error(state, "Line input contains unsupported controls."), False
            # Ordinary-line hosts submit complete commands and never own drafts.
            return action(state, _line_action(payload))
        if kind == "text":
            if not _safe_input(payload):
                return _input_error(state, "Text input contains unsupported controls."), False
            if state.prompt is not None:
                return _insert_prompt(state, payload), False
            bindings = {
                "q": "close", "j": "cursor_down", "k": "cursor_up", " ": "page_down",
                "b": "page_up", "n": "next_match", "N": "previous_match",
                "m": "toggle_mode", "h": "cursor_left", "l": "cursor_right", "J": "next_sibling", "K": "previous_sibling", "?": "help",
                "c": "clear_search", "/": "begin_search", "g": "begin_goto",
            }
            for index, character in enumerate(payload):
                state, closed = action(state, bindings.get(character, "unknown"))
                if closed:
                    return state, True
                if state.prompt is not None:
                    return _insert_prompt(state, payload[index + 1:]), False
            return state, False
        if payload == "eof" or (payload == "interrupt" and state.prompt is not None):
            return action(state, "close")
        if payload == "interrupt":
            return action(state, "cancel")
        prompt = state.prompt
        if prompt is not None:
            if payload in {"escape", "unknown_escape"}:
                return replace(state, prompt=None), False
            if payload == "enter":
                state, submitted = _submit_prompt(state, snapshot)
                if submitted is None:
                    return state, False
                result, closed = action(state, submitted)
                if result.message_is_error and not closed:
                    result = replace(state, prompt=replace(prompt, error=result.message))
                return result, closed
            return replace(state, prompt=_edit_prompt(prompt, payload)), False
        bindings = {
            "up": "up", "down": "down", "page_up": "page_up",
            "page_down": "page_down", "escape": "cancel",
            "unknown_escape": "unknown",
            "enter": "toggle_fold",
        }
        if payload in bindings:
            return action(state, bindings[payload])
        return state, False
    if len(event) > _MAX_EVENT_CHARACTERS:
        return _input_error(state, "Unsupported host event; press ? for help."), False
    # Old hosts keep their closed semantic vocabulary in either protocol mode.
    if event in {"begin_search", "begin_goto"}:
        event = "unknown"
    return action(state, event)


def _cursor_transition(state: ViewState, event: str, rendered: RenderResult | None) -> ViewState:
    if rendered is None or not rendered.characters:
        return state
    characters = rendered.characters
    cursor = state.cursor or rendered.cursor
    current = next((cell for cell in characters if cell.position == cursor), characters[0])
    if event == "toggle_fold":
        container = next((item for item in rendered.containers
                          if item.identity == current.container), None)
        if container is None or not container.nonempty:
            return state
        folds = state.folds.symmetric_difference((container.identity,))
        return replace(state, folds=folds, cursor=None, preferred_column=None,
                       focus_container=container.identity, focus_property=None, reveal_match=False,
                       message=None, message_is_error=False)
    rows = sorted({cell.screen_row for cell in characters})
    preferred = current.position.column if state.preferred_column is None else state.preferred_column
    if event in {"cursor_left", "cursor_right"}:
        index = characters.index(current)
        index = max(0, min(len(characters) - 1, index + (-1 if event == "cursor_left" else 1)))
        destination = characters[index]
        preferred = destination.position.column
    else:
        index = rows.index(current.screen_row)
        index = max(0, min(len(rows) - 1, index + (-1 if event == "cursor_up" else 1)))
        row = [cell for cell in characters if cell.screen_row == rows[index]]
        destination = min(row, key=lambda cell: (abs(cell.position.column - preferred), cell.position.column))
    return replace(state, cursor=destination.position, preferred_column=preferred,
                   focus_container=None, reveal_match=False, message=None, message_is_error=False)


def _sibling_transition(state: ViewState, event: str, rendered: RenderResult | None) -> ViewState:
    if rendered is None:
        return state
    cell = next((cell for cell in rendered.characters if cell.position == state.cursor), None)
    if cell is None:
        return state
    owner = next((item for item in rendered.properties if item.identity == cell.property), None)
    root_entry = cell.property is None and cell.container == FoldIdentity(cell.position.record_index, ()) and cell.delimiter is not None
    if owner is None and not root_entry:
        return state
    siblings = [item for item in rendered.properties
                if item.identity.record_index == cell.position.record_index
                and item.parent == (() if owner is None else owner.parent)]
    if not siblings:
        return state
    if owner is None:
        index = 0 if event == "next_sibling" else len(siblings) - 1
    else:
        index = (siblings.index(owner) + (1 if event == "next_sibling" else -1)) % len(siblings)
    target = siblings[index]
    message = (
        "Only sibling." if len(siblings) == 1 else
        "First sibling." if index == 0 else
        "Last sibling." if index == len(siblings) - 1 else None
    )
    return replace(state, selected_index=target.identity.record_index,
                   cursor=None, preferred_column=None, focus_container=None,
                   focus_property=target.identity, reveal_match=False,
                   message=message, message_is_error=False)


def _unfold_match(state: ViewState, search: SearchState) -> frozenset[FoldIdentity]:
    hit = search.current_occurrence
    if hit is None:
        return state.folds
    return frozenset(fold for fold in state.folds
                     if fold.record_index != hit.record_index
                     or hit.path[:len(fold.path)] != fold.path
                     or (hit.kind == "key" and hit.path == fold.path))


def _semantic_transition(
    state: ViewState,
    event: str | None,
    snapshot: Snapshot,
    spec: ViewerSpec,
    *,
    page_size: int,
    selected_line_count: int,
    malformed: bool = False,
    rendered: RenderResult | None = None,
) -> tuple[ViewState, bool]:
    if event == "idle":
        return state, False
    if event is None or event == "close":
        return state, True
    if malformed and event not in {"help", "cancel"}:
        return _message(state, "Malformed input is read-only; press q to close."), False

    if state.prompt is not None:
        if event == "cancel":
            return replace(state, prompt=None), False
        return state, False

    if state.help_visible:
        if event in {"help", "cancel"}:
            return replace(state, help_visible=False, message=None), False
        if event == "close":
            return state, True
        return state, False

    if event == "help":
        return replace(state, help_visible=True, message=None), False
    if event == "cancel":
        if state.message in {"First sibling.", "Last sibling.", "Only sibling."}:
            return _clear_message(state), False
        if state.search is not None:
            return replace(state, search=None, message="Search cleared."), False
        if state.message is not None:
            return _clear_message(state), False
        return state, True
    if event == "clear_search":
        return replace(state, search=None, message="Search cleared."), False

    if event in {"next_sibling", "previous_sibling"}:
        return _sibling_transition(state, event, rendered), False
    if not snapshot.records:
        return _message(state, "The immutable snapshot has no records."), False

    if event in {"begin_search", "begin_goto"}:
        kind = "search_query" if event == "begin_search" else "goto"
        return replace(state, prompt=PromptState(kind)), False

    if event in {"cursor_left", "cursor_right", "cursor_up", "cursor_down", "toggle_fold"}:
        return _cursor_transition(state, event, rendered), False
    if event in {"up", "down"}:
        state = replace(state, reveal_match=False)
        direction = -1 if event == "up" else 1
        selected = min(
            len(snapshot.records) - 1,
            max(0, state.selected_index + direction),
        )
        if selected == state.selected_index:
            return _clear_message(state), False
        return replace(
            state,
            selected_index=selected,
            record_line_offset=0,
            cursor=None, preferred_column=None, focus_container=None, focus_property=None,
            message=None,
        ), False

    if event in {"page_up", "page_down"}:
        state = replace(state, reveal_match=False)
        step = max(1, page_size - 1)
        if event == "page_up":
            if state.record_line_offset > 0:
                offset = max(0, state.record_line_offset - step)
                return replace(state, record_line_offset=offset, cursor=None, preferred_column=None, focus_container=None, focus_property=None, message=None), False
            if state.selected_index > 0:
                return replace(
                    state,
                    selected_index=state.selected_index - 1,
                    record_line_offset=0,
                    cursor=None, preferred_column=None, focus_container=None, focus_property=None,
                    message=None,
                ), False
            return _clear_message(state), False
        if state.record_line_offset + step < selected_line_count:
            return replace(
                state,
                record_line_offset=state.record_line_offset + step,
                cursor=None, preferred_column=None, focus_container=None, focus_property=None,
                message=None,
            ), False
        if state.selected_index + 1 < len(snapshot.records):
            return replace(
                state,
                selected_index=state.selected_index + 1,
                record_line_offset=0,
                cursor=None, preferred_column=None, focus_container=None, focus_property=None,
                message=None,
            ), False
        return _clear_message(state), False

    if event == "toggle_mode":
        if (
            state.mode is ViewMode.VERBOSE
            and state.search is not None
            and state.search.current_record_index is not None
            and _has_hidden_match(
                state.search, state.search.current_record_index, snapshot, spec
            )
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
            cursor=None, preferred_column=None, focus_container=None, focus_property=None,
            reveal_match=False,
            message=f"Switched to {mode.value} mode.",
        ), False

    if event in {"next_match", "previous_match"}:
        search = state.search
        if search is None or not search.occurrences:
            return _message(state, "No search matches are active."), False
        assert search.current_index is not None
        delta = 1 if event == "next_match" else -1
        current = (search.current_index + delta) % len(search.occurrences)
        selected = search.occurrences[current].record_index
        search = replace(search, current_index=current)
        promote = state.mode is ViewMode.SIMPLE and _has_hidden_match(
            search, selected, snapshot, spec
        )
        return replace(
            state,
            selected_index=selected,
            record_line_offset=0,
            cursor=None, preferred_column=None, focus_container=None, focus_property=None,
            mode=ViewMode.VERBOSE if promote else state.mode,
            search=search,
            reveal_match=True, folds=_unfold_match(state, search),
            message=(
                "Hidden-field match selected; switched to Verbose mode." if promote else None
            ),
            message_is_error=False,
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
                    cursor=None, preferred_column=None, focus_container=None, focus_property=None,
                    reveal_match=False,
                    message=f"Moved to source line {source_line}.",
                ), False
        return _message(
            state, f"Source line {source_line} is not a JSONL record."
        ), False

    if event.startswith("search\t"):
        parts = event.split("\t")
        if len(parts) != 2:
            return _message(
                state, "Search requires only a query; field search was removed.", error=True
            ), False
        _, query = parts
        if not query:
            return _message(state, "Search query must not be empty.", error=True), False
        if len(query) > _MAX_QUERY_CHARACTERS:
            return _message(state, "Search query is too long.", error=True), False
        if not _safe_input(query):
            return _message(state, "Search query contains unsupported controls.", error=True), False
        try:
            occurrences = find_matches(snapshot, query)
        except SearchLimitError as exc:
            return _message(state, str(exc), error=True), False
        search = SearchState(
            query=query,
            occurrences=occurrences,
            current_index=0 if occurrences else None,
        )
        if not occurrences:
            return replace(
                state,
                search=search,
                message="No matches in all text.",
                message_is_error=False,
            ), False
        mode = state.mode
        message = None
        selected = occurrences[0].record_index
        if mode is ViewMode.SIMPLE and _has_hidden_match(search, selected, snapshot, spec):
            mode = ViewMode.VERBOSE
            message = "Hidden-field match selected; switched to Verbose mode."
        return replace(
            state,
            selected_index=selected,
            record_line_offset=0,
            cursor=None, preferred_column=None, focus_container=None, focus_property=None,
            mode=mode,
            search=search,
            reveal_match=True, folds=_unfold_match(state, search),
            message=message,
            message_is_error=False,
        ), False

    return _message(state, "Unknown event; press ? for help.", error=True), False


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
        rendered = None
        geometry = None
        cursor_visible = True
        present_needed = True
        while not closed:
            current_geometry = (*host.terminal_size(), bool(host.color_enabled()))
            if rendered is None or current_geometry != geometry:
                columns, rows, color = current_geometry
                rendered = render_frame(spec, state, snapshot=snapshot, diagnostic=diagnostic,
                                        columns=columns, rows=rows, color=color)
                geometry = current_geometry
                present_needed = True
                state = replace(state, record_line_offset=rendered.record_line_offset,
                                cursor=rendered.cursor if rendered.characters else state.cursor,
                                focus_container=None)
            if present_needed:
                host.present(rendered.text if cursor_visible else (rendered.idle_text or rendered.text))
            event = host.read_event()
            if event == "idle":
                present_needed = False
                if state.prompt is None and not state.help_visible and rendered.characters:
                    cursor_visible = not cursor_visible
                    present_needed = True
                continue
            cursor_visible = True
            present_needed = True
            previous = state
            state, closed = _transition(
                state, event, snapshot, spec, page_size=rendered.body_rows,
                selected_line_count=rendered.selected_line_count,
                malformed=diagnostic is not None, rendered=rendered,
            )
            if state.focus_property == previous.focus_property and (
                state.selected_index != previous.selected_index
                or state.record_line_offset != previous.record_line_offset
                or state.mode != previous.mode or state.search != previous.search
                or state.reveal_match):
                state = replace(state, focus_property=None)
            rendered = None
    finally:
        host.close_view()
