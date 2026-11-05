"""Coordinate one generic inspection using materialized immutable data."""

from __future__ import annotations

from dataclasses import replace

from ._browser_render import render_browser
from ._catalog import Catalog, bind_catalog
from ._controls import _accept_frame, _edit_prompt, _line_action, _safe_input, _transition, _unfold_match
from ._model import BrowserFrame, BrowserState, CursorPosition, InputDiagnostic, PromptState, Snapshot
from ._mouse import parse_mouse
from ._query import evaluate
from ._render import RenderSession, _text_cells
from ._search import SearchLimitError
from .contracts import ViewerHost, ViewerSpec


def _select(state: BrowserState, catalog: Catalog, index: int, *, section: int | None = None,
            first_hit: bool = False, focus: str | None = None) -> BrowserState:
    item = catalog.items[index]
    section_index = min(section if section is not None else 0, len(item.sections) - 1)
    search = state.result.search if state.result else None
    member_set = frozenset(item.records)
    hit = next((h for h in search.occurrences if h.record_index in member_set), None) if search and first_hit else None
    if hit:
        section_index = next((i for i, s in enumerate(item.sections) if hit.record_index in s.records), 0)
        search = replace(search, current_index=search.occurrences.index(hit))
    records = item.sections[section_index].records
    record = hit.record_index if hit else records[0] if records else item.records[0]
    detail = replace(state.detail, selected_index=record, record_line_offset=0,
                     horizontal_offset=0, cursor=None, preferred_column=None,
                     search=search, reveal_match=bool(hit), message=None,
                     focus_container=None, focus_property=None, gutter_columns=0)
    if hit:
        detail = replace(detail, folds=_unfold_match(detail, search))
    result = replace(state.result, search=search) if state.result else None
    return replace(state, selected=index, section=section_index, detail=detail,
                   result=result, focus=focus or state.focus, notice=None, error=None)


def _commit(state: BrowserState, snapshot: Snapshot, catalog: Catalog,
            filters: tuple[frozenset[int], ...], query: str | None, scope: int) -> BrowserState:
    try:
        result = evaluate(snapshot, catalog, filters, query, scope)
    except (ValueError, SearchLimitError) as exc:
        return replace(state, error=str(exc))
    old_hit = state.result.search.current_occurrence if state.result and state.result.search else None
    if old_hit is not None and result.search and old_hit in result.search.occurrences:
        result = replace(result, search=replace(result.search, current_index=result.search.occurrences.index(old_hit)))
    retain = state.selected in result.items and (query is None or (query == state.query and scope == state.scope))
    committed = replace(state, filters=filters, query=query, scope=scope, result=result,
                        panel=None, error=None, notice=None, reset_all=False)
    if not result.items:
        return replace(committed, detail=replace(state.detail, search=result.search))
    if retain:
        return replace(committed, detail=replace(state.detail, search=result.search))
    return _select(committed, catalog, result.items[0], first_hit=query is not None)


def _move_item(state: BrowserState, catalog: Catalog, delta: int) -> BrowserState:
    items = state.result.items
    if not items:
        return state
    old = items.index(state.selected) if state.selected in items else 0
    index = items[(old + delta) % len(items)]
    return _select(state, catalog, index, first_hit=state.query is not None)


def _move_match(state: BrowserState, catalog: Catalog, delta: int) -> BrowserState:
    search = state.result.search
    if search is None or not search.occurrences:
        return state
    index = ((search.current_index or 0) + delta) % len(search.occurrences)
    updated = replace(search, current_index=index)
    hit = updated.occurrences[index]
    item_index = catalog.record_items[hit.record_index]
    state = replace(state, result=replace(state.result, search=updated))
    item = catalog.items[item_index]
    section = next((i for i, s in enumerate(item.sections) if hit.record_index in s.records), 0)
    state = _select(state, catalog, item_index, section=section, focus="detail")
    detail = replace(state.detail, selected_index=hit.record_index, search=updated,
                     reveal_match=True, cursor=None,
                     folds=_unfold_match(state.detail, updated))
    return replace(state, detail=detail)


def _filter_action(state: BrowserState, action: int, snapshot: Snapshot, catalog: Catalog) -> BrowserState:
    if action == 0:
        return _commit(state, snapshot, catalog, state.draft_filters,
                       None if state.reset_all else state.query,
                       -1 if state.reset_all else state.scope)
    if action in (1, 2):
        return replace(state, draft_filters=tuple(frozenset() for _ in catalog.facets),
                       reset_all=state.reset_all or action == 2, error=None)
    return replace(state, panel=None, error=None, reset_all=False)


def _open_field(state: BrowserState, index: int) -> BrowserState:
    return replace(state, panel="options", field=index, panel_index=0,
                   draft_options=state.draft_filters[index], error=None)


def _option(state: BrowserState, catalog: Catalog, index: int) -> BrowserState:
    if index < 0:
        selected = frozenset()
    elif not catalog.facets[state.field].multiple:
        selected = frozenset() if index in state.draft_options else frozenset((index,))
    else:
        selected = state.draft_options.symmetric_difference((index,))
    return replace(state, draft_options=selected, error=None)


def _modal_key(state: BrowserState, key: str, text: str | None,
               snapshot: Snapshot, catalog: Catalog) -> tuple[BrowserState, bool]:
    if key in {"eof", "interrupt"}:
        return state, True
    if key in {"escape", "unknown_escape", "cancel"}:
        if state.panel == "options":
            return replace(state, panel="filters", panel_index=state.field, error=None), False
        return replace(state, panel=None, error=None), False
    if state.panel == "filters":
        count = len(catalog.facets) + 4
        if key in {"up", "down", "tab", "shift_tab", "page_up", "page_down"}:
            delta = -1 if key in {"up", "shift_tab", "page_up"} else 1
            return replace(state, panel_index=(state.panel_index + delta) % count), False
        if key == "enter":
            if state.panel_index < len(catalog.facets):
                return _open_field(state, state.panel_index), False
            return _filter_action(state, state.panel_index - len(catalog.facets), snapshot, catalog), False
        return state, False
    if state.panel == "options":
        count = len(catalog.facets[state.field].options) + 1
        if key in {"up", "down", "tab", "shift_tab", "page_up", "page_down"}:
            delta = -1 if key in {"up", "shift_tab", "page_up"} else 1
            return replace(state, panel_index=(state.panel_index + delta) % count), False
        if text == " " or key == "space":
            return _option(state, catalog, state.panel_index - 1), False
        if key == "enter":
            filters = list(state.draft_filters)
            filters[state.field] = state.draft_options
            return replace(state, panel="filters", panel_index=state.field,
                           draft_filters=tuple(filters), error=None), False
        return state, False
    if state.panel in {"annotations", "help"}:
        if key in {"up", "down", "page_up", "page_down"}:
            delta = -1 if key in {"up", "page_up"} else 1
            return replace(state, panel_index=max(0, state.panel_index + delta)), False
        return state, False
    if key in {"tab", "shift_tab"}:
        count = 3 if state.panel == "goto" else 4
        return replace(state, panel_index=(state.panel_index + (-1 if key == "shift_tab" else 1)) % count), False
    if state.panel_index == 0:
        if text is not None:
            if not _safe_input(text) or len(state.draft_query.buffer) + len(text) > 1_024:
                return replace(state, error="Search draft exceeds its input bound."), False
            prompt = state.draft_query
            prompt = replace(prompt, buffer=prompt.buffer[:prompt.cursor] + text + prompt.buffer[prompt.cursor:],
                             cursor=prompt.cursor + len(text), error=None)
            return replace(state, draft_query=prompt, error=None), False
        if key != "enter":
            return replace(state, draft_query=_edit_prompt(state.draft_query, key), error=None), False
    if state.panel == "goto":
        if key == "enter":
            if state.panel_index == 2:
                return replace(state, panel=None, error=None), False
            value = state.draft_query.buffer
            if not value.isascii() or not value.isdigit() or int(value) < 1:
                return replace(state, error="Enter a positive source line."), False
            index = next((i for i, r in enumerate(snapshot.records) if r.source_line == int(value)), None)
            if index is None or catalog.record_items[index] not in state.result.items:
                return replace(state, error="Source line is outside the current results."), False
            item_index = catalog.record_items[index]
            item = catalog.items[item_index]
            section = next((i for i, s in enumerate(item.sections) if index in s.records), 0)
            selected = _select(replace(state, panel=None, error=None), catalog, item_index,
                               section=section, focus="detail")
            return replace(selected, detail=replace(selected.detail, selected_index=index)), False
        return state, False
    if state.panel_index == 1 and key in {"left", "right", "up", "down", "enter"}:
        delta = -1 if key in {"left", "up"} else 1
        count = len(catalog.scopes) + 1
        return replace(state, draft_scope=(state.draft_scope + 1 + delta) % count - 1, error=None), False
    if key == "enter":
        if state.panel_index == 3:
            return replace(state, panel=None, error=None), False
        return _commit(state, snapshot, catalog, state.filters, state.draft_query.buffer, state.draft_scope), False
    return state, False


def _detail_event(state: BrowserState, event: str, snapshot: Snapshot, catalog: Catalog,
                  spec: ViewerSpec, frame: BrowserFrame, session: RenderSession) -> BrowserState:
    if frame.detail is None:
        return state
    detail, _ = _transition(state.detail, event, snapshot, spec,
                            page_size=frame.detail.body_rows,
                            selected_line_count=frame.detail.selected_line_count,
                            rendered=frame.detail, session=session)
    allowed = tuple(record for index in state.result.items for record in catalog.items[index].records)
    if detail.selected_index not in allowed:
        forward = event not in {"up", "page_up", "cursor_up", "cursor_left", "text\th", "text\tk"}
        candidates = [i for i in allowed if i > state.detail.selected_index] if forward else [i for i in allowed if i < state.detail.selected_index]
        index = (min(candidates) if forward else max(candidates)) if candidates else (min(allowed) if forward else max(allowed))
        offset, cursor = 0, None
        if event in {"page_up", "cursor_up", "cursor_left", "text\th", "text\tk", "text\tb"}:
            projection = session.projection(snapshot.records[index], index, spec, detail)
            last = projection.navigable_rows[-1] if projection.navigable_rows else 0
            offset = max(0, len(projection.lines) - frame.detail.body_rows)
            column = detail.preferred_column
            if event in {"cursor_left", "text\th"}:
                column = max(0, sum(_text_cells(segment.text) for segment in projection.lines[last].segments) - 1)
            cursor = CursorPosition(index, last, column or 0)
        detail = replace(detail, selected_index=index, cursor=cursor, record_line_offset=offset,
                         horizontal_offset=0, reveal_cursor=cursor is not None)
    item_index = catalog.record_items[detail.selected_index]
    item = catalog.items[item_index]
    section = state.section if item_index == state.selected and detail.selected_index in item.sections[state.section].records else next(
        (i for i, s in enumerate(item.sections) if detail.selected_index in s.records), 0)
    return replace(state, detail=detail, selected=item_index, section=section)


def _mouse_event(state: BrowserState, event: str, snapshot: Snapshot, catalog: Catalog,
                 spec: ViewerSpec, frame: BrowserFrame, session: RenderSession) -> BrowserState:
    mouse = parse_mouse(event)
    if mouse is None or mouse.action is None:
        return state
    if not (1 <= mouse.column <= frame.columns and 1 <= mouse.row <= frame.rows):
        return state
    row, column = mouse.row - 1, mouse.column - 1
    hit = next((h for h in frame.hits if h.row == row and h.column <= column < h.column + h.width), None)
    if mouse.action in {"cursor_up", "cursor_down"}:
        key = "up" if mouse.action == "cursor_up" else "down"
        if state.panel:
            if hit is None:
                return state
            return _modal_key(state, key, None, snapshot, catalog)[0] if state.panel not in {"search", "goto"} else state
        if row < frame.body_row or row >= frame.rows - 2:
            return state
        if frame.detail and row >= frame.detail_row and column >= frame.detail_column:
            return _detail_event(replace(state, focus="detail"), event.replace(
                f"\t{mouse.column}\t{mouse.row}\t", f"\t{mouse.column-frame.detail_column}\t{mouse.row-frame.detail_row}\t"), snapshot, catalog, spec, frame, session)
        return _move_item(replace(state, focus="list"), catalog, -1 if key == "up" else 1)
    if hit is not None:
        if hit.kind == "field":
            return _open_field(state, hit.index)
        if hit.kind == "option":
            return _option(replace(state, panel_index=hit.index + 1), catalog, hit.index)
        if hit.kind == "filter_action":
            return _filter_action(state, hit.index, snapshot, catalog)
        if hit.kind == "search_field":
            state = replace(state, panel_index=hit.index)
            activate = hit.index in (1, 2) if state.panel == "goto" else hit.index in (1, 2, 3)
            return _modal_key(state, "enter", None, snapshot, catalog)[0] if activate else state
        if hit.kind == "item":
            return _select(state, catalog, hit.index, first_hit=state.query is not None, focus="list")
        if hit.kind == "section":
            return _select(state, catalog, state.selected, section=hit.index, focus="detail")
        if hit.kind == "annotations":
            return replace(state, panel="annotations", panel_index=0, error=None)
        if hit.kind == "filters":
            return replace(state, panel="filters", panel_index=0, draft_filters=state.filters, reset_all=False, error=None)
        if hit.kind == "search":
            return replace(state, panel="search", panel_index=0,
                           draft_query=PromptState("search_query", state.query or "", len(state.query or "")),
                           draft_scope=state.scope, error=None)
    if not state.panel and frame.detail and row >= frame.detail_row and column >= frame.detail_column:
        local = f"mouse\t{mouse.button}\t{mouse.column-frame.detail_column}\t{mouse.row-frame.detail_row}\t{mouse.phase}"
        return _detail_event(replace(state, focus="detail"), local, snapshot, catalog, spec, frame, session)
    return state


def transition_browser(state: BrowserState, event: str | None, snapshot: Snapshot,
                       catalog: Catalog, spec: ViewerSpec, frame: BrowserFrame,
                       session: RenderSession) -> tuple[BrowserState, bool]:
    if event is None or event == "close":
        return state, True
    if type(event) is not str or len(event) > 8_192 + 6:
        return replace(state, notice="Unsupported input."), False
    if event == "idle":
        return state, False
    if event.startswith("mouse\t"):
        if spec.input_protocol != "keys":
            return replace(state, notice="Unsupported input envelope."), False
        return _mouse_event(state, event, snapshot, catalog, spec, frame, session), False
    text: str | None = None
    if event.startswith("key\t"):
        if spec.input_protocol != "keys":
            return replace(state, notice="Unsupported input envelope."), False
        key = event[4:]
        if key not in {"enter", "escape", "unknown_escape", "backspace", "delete",
                       "left", "right", "home", "end", "up", "down", "page_up",
                       "page_down", "ctrl_a", "ctrl_e", "ctrl_u", "ctrl_k", "ctrl_w",
                       "tab", "shift_tab", "space", "interrupt", "eof", "unknown"}:
            return replace(state, notice="Unsupported physical key."), False
    elif event.startswith("text\t"):
        text, key = event[5:], ""
        if spec.input_protocol != "keys" or len(text) > 8_192 or not _safe_input(text):
            return replace(state, notice="Unsupported text input."), False
    elif event.startswith("line\t"):
        command = event[5:]
        if spec.input_protocol != "keys" or len(command) > 8_192 or not _safe_input(command, allow_tab=True):
            return replace(state, notice="Unsupported line input."), False
        aliases = {"f":"filters", "filters":"filters", "tab":"focus_next", "shift-tab":"focus_previous",
                   "enter":"enter", "space":"space", "[":"section_previous", "]":"section_next", "a":"a"}
        action = aliases.get(command.strip(), _line_action(command))
        if action == "unknown" and state.panel == "search" and state.panel_index == 0:
            inserted, _ = _modal_key(state, "", command, snapshot, catalog)
            return _modal_key(inserted, "enter", None, snapshot, catalog)
        return transition_browser(state, action, snapshot, catalog, spec, frame, session)
    elif event.startswith("search\t"):
        return _commit(state, snapshot, catalog, state.filters, event[7:], state.scope), False
    elif event.startswith("goto\t"):
        prompt = replace(state, panel="goto", panel_index=0, draft_query=PromptState("goto", event[5:], len(event[5:])))
        return _modal_key(prompt, "enter", None, snapshot, catalog)
    else:
        key = {"cancel":"escape", "help":"?", "clear_search":"c", "next_match":"n",
               "previous_match":"N", "filters":"f", "begin_filter":"f",
               "focus_next":"tab", "focus_previous":"shift_tab", "section_next":"]",
               "section_previous":"[", "toggle_mode":"m"}.get(event, event)
    if state.panel:
        return _modal_key(state, key, text, snapshot, catalog)
    if text is not None:
        current = state
        for offset, character in enumerate(text):
            current, closed = transition_browser(current, character, snapshot, catalog, spec, frame, session)
            if closed:
                return current, True
            if current.panel:
                remainder = text[offset + 1:]
                if remainder:
                    current, closed = _modal_key(current, "", remainder, snapshot, catalog)
                return current, closed
            frame = render_browser(spec, current, snapshot, catalog, columns=frame.columns,
                                   rows=frame.rows, color=frame.color, session=session)
        return current, False
    if key in {"q", "eof"}:
        return state, True
    if key == "interrupt":
        key = "escape"
    if key in {"escape", "unknown_escape"}:
        if state.notice or state.detail.message:
            return replace(state, notice=None, detail=replace(state.detail, message=None)), False
        if state.query is not None:
            return _commit(state, snapshot, catalog, state.filters, None, state.scope), False
        return state, True
    if key == "f":
        return replace(state, panel="filters", panel_index=0, draft_filters=state.filters, reset_all=False, error=None), False
    if key in {"/", "g"}:
        value = state.query or "" if key == "/" else ""
        return replace(state, panel="search" if key == "/" else "goto", panel_index=0,
                       draft_query=PromptState("search_query" if key == "/" else "goto", value, len(value)),
                       draft_scope=state.scope, error=None), False
    if key in {"a", "?"}:
        return replace(state, panel="annotations" if key == "a" and state.result.items else "help", panel_index=0), False
    if key in {"tab", "shift_tab"}:
        return replace(state, focus="detail" if state.focus == "list" else "list"), False
    if key == "c":
        return _commit(state, snapshot, catalog, state.filters, None, state.scope), False
    if key in {"n", "N"}:
        return _move_match(state, catalog, 1 if key == "n" else -1), False
    if len(key) == 1 and not key.isascii():
        return replace(state, notice="Commands use English; switch your keyboard input to English."), False
    if not state.result.items:
        return state, False
    if key in {"[", "]"}:
        count = len(catalog.items[state.selected].sections)
        return _select(state, catalog, state.selected,
                       section=(state.section + (1 if key == "]" else -1)) % count, focus="detail"), False
    if state.focus == "list" and key in {"up", "down", "page_up", "page_down"}:
        return _move_item(state, catalog, -1 if key in {"up", "page_up"} else 1), False
    if key in {"up", "down", "page_up", "page_down", "cursor_left", "cursor_right",
               "cursor_up", "cursor_down", "toggle_fold", "next_sibling", "previous_sibling",
               "scroll_left", "scroll_right"}:
        detail_event = key
    elif key == "enter":
        if state.focus == "list":
            return _select(state, catalog, state.selected, first_hit=state.query is not None, focus="detail"), False
        detail_event = "toggle_fold"
    elif len(key) == 1:
        detail_event = "text\t" + key if spec.input_protocol == "keys" else {
            "h":"cursor_left", "j":"cursor_down", "k":"cursor_up", "l":"cursor_right",
            "m":"toggle_mode", "J":"next_sibling", "K":"previous_sibling",
            " ":"page_down", "b":"page_up"}.get(key, key)
    else:
        return state, False
    return _detail_event(state, detail_event, snapshot, catalog, spec, frame, session), False


def view_browser(snapshot: Snapshot, spec: ViewerSpec, host: ViewerHost,
                 diagnostic: InputDiagnostic | None = None) -> None:
    """Run without acquiring or closing the caller-owned host."""
    catalog = bind_catalog(snapshot, spec.inspection)
    filters = tuple(frozenset() for _ in catalog.facets)
    state = BrowserState(filters=filters, draft_filters=filters,
                         result=evaluate(snapshot, catalog, filters, None),
                         focus="detail" if spec.inspection is None else "list",
                         diagnostic=diagnostic)
    if state.result.items:
        state = _select(state, catalog, state.result.items[0])
    session = RenderSession()
    notifier = getattr(host, "set_input_mode", None)
    input_mode = None
    while True:
        desired = "text" if state.panel == "search" and state.panel_index == 0 else "command"
        if desired != input_mode:
            if callable(notifier):
                notifier(desired)
            input_mode = desired
        columns, rows = host.terminal_size()
        frame = render_browser(spec, state, snapshot, catalog, columns=columns, rows=rows,
                               color=bool(host.color_enabled()), session=session)
        if frame.detail is not None and not state.panel:
            state = replace(state, detail=_accept_frame(state.detail, frame.detail))
        host.present(frame.text)
        state, closed = transition_browser(state, host.read_event(), snapshot, catalog, spec, frame, session)
        if closed:
            return
