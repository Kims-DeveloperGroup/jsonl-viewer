"""Deterministic generic list, modal, and detail-pane presentation."""

from __future__ import annotations

from dataclasses import replace

from ._catalog import Catalog
from ._cell_layout import clusters, text_cells
from ._model import BrowserFrame, BrowserHit, BrowserState, Snapshot
from ._render import RenderSession, _clip_text, _neutralize_text, clip_painted_frame, render_frame, strip_ansi
from ._selection import selection_label
from .contracts import ViewerSpec


def _fit(text: str, width: int) -> str:
    return _clip_text(_neutralize_text(text), max(0, width))


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - text_cells(strip_ansi(text)))


def _wrap(text: str, width: int) -> list[str]:
    rows: list[str] = []
    current = ""
    size = 0
    for cluster in clusters(_neutralize_text(text)):
        cells = text_cells(cluster)
        if current and size + cells > width:
            rows.append(current)
            current, size = "", 0
        current += cluster
        size += cells
    rows.append(current)
    return rows


def _draft_window(buffer: str, cursor: int, width: int) -> str:
    left = list(clusters(_neutralize_text(buffer[:cursor])))
    right = list(clusters(_neutralize_text(buffer[cursor:])))
    before: list[str] = []
    cells = 0
    for cluster in reversed(left):
        if cells + text_cells(cluster) > max(0, width // 2):
            break
        before.append(cluster)
        cells += text_cells(cluster)
    after: list[str] = []
    for cluster in right:
        if cells + 1 + text_cells(cluster) > width:
            break
        after.append(cluster)
        cells += text_cells(cluster)
    return "".join(reversed(before)) + "│" + "".join(after)


def _selection(catalog: Catalog, filters: tuple[frozenset[int], ...], index: int) -> str:
    selected = filters[index]
    return ", ".join(catalog.facets[index].options[o].label for o in sorted(selected)) if selected else "All"


def _scope(catalog: Catalog, index: int) -> str:
    return catalog.scopes[index].label if index >= 0 else "All text"


def _occurrence(state: BrowserState) -> str:
    search = state.result.search if state.result else None
    if search is None:
        return ""
    if not search.occurrences:
        return "Occurrences: 0"
    return f"Occurrence {(search.current_index or 0) + 1}/{len(search.occurrences)}"


def _modal(spec: ViewerSpec, catalog: Catalog, state: BrowserState,
           width: int, height: int) -> BrowserFrame:
    entries: list[tuple[str, str, int]] = []
    focus = state.panel_index
    if state.panel == "filters":
        title = "Filters — changes apply only with Apply"
        entries.extend((f"{f.label}: [{_selection(catalog, state.draft_filters, i)}]", "field", i)
                       for i, f in enumerate(catalog.facets))
        entries.extend((label, "filter_action", i) for i, label in enumerate(
            ("[Apply]", "[Reset filters]", "[Reset all]", "[Cancel]")))
        suffix = "will clear on Apply" if state.reset_all else "unchanged"
        explanation = f"Search: {state.query!r} in {_scope(catalog, state.scope)} — {suffix}"
        help_text = "↑/↓ move · Enter choose · Tab actions · Esc cancel"
    elif state.panel == "options":
        facet = catalog.facets[state.field]
        title = facet.label + (" — multiple selection" if facet.multiple else " — single selection")
        entries.append((f"[{'x' if not state.draft_options else ' '}] All", "option", -1))
        entries.extend((f"[{'x' if i in state.draft_options else ' '}] {o.label}", "option", i)
                       for i, o in enumerate(facet.options))
        explanation = "Enter confirms this field; Apply confirms all filters."
        help_text = "↑/↓ move · Space toggle · Enter done · Esc cancel this field"
    elif state.panel in {"search", "goto"}:
        title = "Search — literal text" if state.panel == "search" else "Go to source line"
        prompt = state.draft_query
        draft = _draft_window(prompt.buffer, prompt.cursor, max(1, width - 16))
        entries = [("Query: " + draft, "search_field", 0),
                   ("Scope: < " + _scope(catalog, state.draft_scope) + " >", "search_field", 1),
                   ("[Search]", "search_field", 2), ("[Cancel]", "search_field", 3)] if state.panel == "search" else [
                       ("Source line: " + draft, "search_field", 0),
                       ("[Go]", "search_field", 1), ("[Cancel]", "search_field", 2)]
        explanation = "Select a scope with ←/→; words are never expressions."
        help_text = "Tab focus · ←/→ scope/edit · Enter search · Esc cancel"
    elif state.panel == "help":
        title = "Help"
        entries = [(text, "annotation", -1) for text in (
            "Tab / Shift+Tab: change list/detail focus", "Up/Down: items or original records",
            "v: toggle Single/Split layout in the main view",
            "Enter: open detail or fold JSON", "h/j/k/l: JSON cursor; J/K: sibling keys",
            "[/]: previous/next detail section", "/: edit literal search and scope",
            "f: filter draft; Apply commits all changes", "n/N: next/previous occurrence",
            "t in list: literal name/ID find; Enter detail; Esc clears",
            "G: caller-provided group; R: ungrouped source record",
            ",/.: scroll full selected name/ID/count information",
            "p: cycle Dark/Light/ANSI palette (when color is enabled)",
            "c: clear query, retain filters and scope", "g: original source line",
            "a: full item annotations", "m: Simple/Verbose; Left/Right: pan",
            "Esc: cancel modal/search/notice; q: close from main view",
        )]
        explanation = "Source records remain immutable."
        help_text = "↑/↓ scroll · Esc return"
    else:
        title = "Item annotations"
        item = catalog.items[state.selected]
        rows = ["Identity: " + (item.display_id or item.id), "Label: " + item.label]
        rows.extend(f"{c.label}: {c.value}" for c in item.annotations)
        for row in rows:
            entries.extend((line, "annotation", -1) for line in _wrap(row, max(1, width - 2)))
        explanation = "Display annotations are separate from source JSON."
        help_text = "↑/↓ scroll · Esc return"
    current = _occurrence(state)
    if current:
        explanation = current + " · " + explanation
    head = [_fit(spec.title + " · READ ONLY", width), _fit(title, width)]
    capacity = max(1, height - 5)
    start = max(0, min(focus - capacity + 1, max(0, len(entries) - capacity)))
    hits: list[BrowserHit] = []
    lines = head + [_fit(explanation, width)]
    for index, (text, kind, value) in enumerate(entries[start:start + capacity], start):
        prefix = "> " if index == focus else "  "
        hits.append(BrowserHit(kind, value, len(lines), 0, width))
        lines.append(_fit(prefix + text, width))
    while len(lines) < height - 2:
        lines.append("")
    lines.append(_fit("Error: " + state.error if state.error else "Changes not applied" if state.panel in {"filters", "options"} else "", width))
    lines.append(_fit(help_text, width))
    return BrowserFrame("\n".join(lines[:height]), hits=tuple(hits), columns=width, rows=height, body_row=3)


def list_items(state: BrowserState, catalog: Catalog) -> tuple[int, ...]:
    """Name finding narrows presentation, never the materialized body result."""
    query = state.list_query.casefold()
    return tuple(index for index in state.result.items
                 if not query or query in catalog.items[index].label.casefold()
                 or query in (catalog.items[index].display_id or catalog.items[index].id).casefold()) if state.result else ()


def _sgr(state: BrowserState, role: str) -> str:
    if state.appearance == "ansi":
        return "\x1b[" + {"body":"97;40", "list0":"97;44", "list1":"97;104",
                           "selected":"1;97;44", "focus":"1;96;40",
                           "meta":"94;40", "match":"30;103"}[role] + "m"
    light = state.appearance == "light"
    foreground = {"body":"082F49" if light else "E0F2FE",
                  "list0":"082F49" if light else "E0F2FE",
                  "list1":"082F49" if light else "E0F2FE", "selected":"FFFFFF",
                  "focus":"075985" if light else "22D3EE",
                  "meta":"1E40AF" if light else "93C5FD", "match":"111827"}[role]
    background = {"body":"F0F9FF" if light else "0B1220",
                  "list0":"E0F2FE" if light else "102A43",
                  "list1":"BAE6FD" if light else "153750", "selected":"1D4ED8",
                  "focus":"F0F9FF" if light else "0B1220",
                  "meta":"F0F9FF" if light else "0B1220", "match":"FBBF24"}[role]
    rgb = lambda value: ";".join(str(int(value[i:i + 2], 16)) for i in (0, 2, 4))
    return "\x1b[38;2;" + rgb(foreground) + ";48;2;" + rgb(background) + "m"


def _paint(text: str, state: BrowserState, color: bool, role: str = "body") -> str:
    if not color:
        return text
    style = _sgr(state, role)
    return style + text.replace("\x1b[0m", "\x1b[0m" + style) + "\x1b[0m"


def _highlight(text: str, query: str, state: BrowserState, color: bool, role: str) -> str:
    if not color or not query:
        return _paint(text, state, color, role)
    parts = tuple(clusters(text))
    folded = "".join(part.casefold() for part in parts)
    needle = query.casefold()
    matches: list[tuple[int, int]] = []
    start = folded.find(needle)
    while start >= 0:
        matches.append((start, start + len(needle)))
        start = folded.find(needle, start + len(needle))
    offset = 0
    result = [_sgr(state, role)]
    for part in parts:
        end = offset + len(part.casefold())
        hit = any(offset < finish and end > begin for begin, finish in matches)
        result.append((_sgr(state, "match") if hit else _sgr(state, role)) + part)
        offset = end
    return "".join(result) + "\x1b[0m"


def render_browser(spec: ViewerSpec, state: BrowserState, snapshot: Snapshot,
                   catalog: Catalog, *, columns: int, rows: int, color: bool,
                   session: RenderSession) -> BrowserFrame:
    width, height = min(240, max(1, columns)), min(100, max(1, rows))
    if width < 24 or height < 6:
        lines = [_fit(spec.title + " · READ ONLY", width), _fit("Resize terminal to inspect data.", width)]
        if state.detail.copy_notice and state.detail.message:
            lines.insert(0, _fit(state.detail.message, width))
        return BrowserFrame("\n".join(lines[:height]), columns=width, rows=height, color=color)
    if state.panel and state.panel != "list_find":
        modal = _modal(spec, catalog, state, width, height)
        return replace(modal, text="\n".join(_paint(_pad(line, width), state, color)
                                               for line in modal.text.splitlines()), color=color)
    result = state.result
    assert result is not None
    scope = _scope(catalog, state.scope)
    active = [(facet.label, _selection(catalog, state.filters, i))
              for i, facet in enumerate(catalog.facets) if state.filters[i]]
    filters = " | ".join(f"{name}={value}" for name, value in active) or "All"
    unit = "Groups/records" if any(item.grouped for item in catalog.items) else "Records"
    count = f"{unit}: {len(catalog.items)} total · {len(result.filtered)} filtered"
    if state.query is not None:
        count += f" · {len(result.items)} matched · {result.matching_records} records · {len(result.search.occurrences)} occurrences"
    header = [spec.title.upper() + " · READ ONLY",
              f"Search: {state.query!r} | In: {scope} · / Edit · c Clear",
              f"Filters({len(active)}): {filters} · f Edit", count]
    context = " | ".join(f"{c.label}: {c.value}" for c in spec.display_context)
    if context:
        header.insert(1, context)
    header = [_fit(line, width) for line in header[:max(1, height - 4)]]
    hits: list[BrowserHit] = []
    for row, kind in ((1 if not context else 2, "search"), (2 if not context else 3, "filters")):
        if row < len(header):
            hits.append(BrowserHit(kind, 0, row, 0, width))
    body_height = max(1, height - len(header) - 2)
    split = state.layout == "split" and width >= 120 and height >= 12
    show_detail = split or state.focus == "detail"
    preferred_width = 40
    if split:
        preferred_width = min(48, max(40, max((text_cells(_neutralize_text(item.label))
                                              for item in catalog.items), default=0) + 12))
    left_width = max(32, min(preferred_width, width - 83)) if split else width
    right_width = width - left_width - 3 if split else width
    visible = list_items(state, catalog)
    item_rows: list[str] = []
    info_max_offset = 0
    if state.diagnostic is not None:
        item_rows = [_fit("Input error", left_width), _fit(state.diagnostic.summary, left_width)]
        item_rows.extend(_wrap(state.diagnostic.detail, left_width))
    else:
        title = ("[Focus] " if state.focus == "list" else "") + ("Groups/records" if unit == "Groups/records" else "Records")
        count_title = "Match/All" if state.query is not None else "Records"
        title_width = max(0, left_width - len(count_title) - 1)
        item_rows.append(_paint(_pad(_fit(title, title_width), title_width) + " " + count_title,
                                state, color, "focus" if state.focus == "list" else "meta"))
        draft = (_draft_window(state.draft_query.buffer, state.draft_query.cursor, max(1, left_width - 12))
                 if state.panel == "list_find" else repr(state.list_query))
        item_rows.append(_paint(_pad(_fit(f"Name/ID: {draft} · {len(visible)} names", left_width), left_width), state, color, "meta"))
        information: list[str] = []
        if result.items and 0 <= state.selected < len(catalog.items):
            item = catalog.items[state.selected]
            information = _wrap("Name: " + item.label, max(1, left_width - 12))
            information += _wrap("ID: " + (item.display_id or item.id), max(1, left_width - 12))
            information += _wrap("Records: " + str(len(item.records)), max(1, left_width - 12))
        info_height = min(2, max(0, body_height - 3))
        capacity = max(0, body_height - 2 - info_height)
        if not visible:
            message = ("No recorded data" if not snapshot.records else
                       "No items match filters" if not result.filtered else
                       "No text matches" if not result.items else "No names match; t edit / Esc clear")
            message_row = _paint(_pad(_fit(message, left_width), left_width), state, color)
            if capacity:
                item_rows.append(message_row)
            else:
                item_rows[0] = message_row
        else:
            position = visible.index(state.selected) if state.selected in visible else 0
            start = max(0, min(position - capacity + 1, len(visible) - capacity))
            matching = {hit.record_index for hit in result.search.occurrences} if result.search else set()
            counts = {index: (str(len(catalog.items[index].records)) if state.query is None else
                             f"{len(set(catalog.items[index].records).intersection(matching))}/{len(catalog.items[index].records)}")
                      for index in result.items}
            count_width = min(max((len(value) for value in counts.values()), default=1), max(1, left_width // 3))
            number_width = max(2, len(str(len(catalog.items))))
            name_width = max(1, left_width - number_width - count_width - 7)
            duplicate_names: dict[str, int] = {}
            for index in result.items:
                label = catalog.items[index].label
                duplicate_names[label] = duplicate_names.get(label, 0) + 1
            for item_index in visible[start:start + capacity]:
                item = catalog.items[item_index]
                selected = item_index == state.selected
                marker = "▸" if selected else " "
                kind = "G" if item.grouped else "R"
                prefix = f"{marker}{kind} {item_index + 1:0{number_width}d} "
                name = item.label
                if duplicate_names[name] > 1 or (state.list_query and state.list_query.casefold() in (item.display_id or item.id).casefold()):
                    id_width = max(1, name_width // 3)
                    id_parts = tuple(clusters(_neutralize_text((item.display_id or item.id))))
                    short_id = ""
                    for part in reversed(id_parts):
                        if text_cells(part + short_id) > max(0, id_width - 1):
                            break
                        short_id = part + short_id
                    short_id = _fit((item.display_id or item.id), id_width) if text_cells((item.display_id or item.id)) <= id_width else "…" + short_id
                    suffix = " [" + short_id + "]"
                    name = _fit(name, max(0, name_width - text_cells(suffix))) + suffix
                name = _pad(_fit(name, name_width), name_width)
                count_value = _fit(counts[item_index], count_width).rjust(count_width)
                role = "selected" if selected else "list" + str(item_index % 2)
                row = _paint(prefix, state, color, role)
                row += _highlight(name, state.list_query, state, color, role)
                row += _paint(_pad(" " + count_value, left_width - text_cells(prefix) - name_width), state, color, role)
                if split or not show_detail:
                    hits.append(BrowserHit("item", item_index, len(header) + len(item_rows), 0, left_width))
                item_rows.append(row)
        while len(item_rows) < body_height - info_height:
            item_rows.append(_paint(" " * left_width, state, color))
        if information and info_height:
            info_max_offset = max(0, len(information) - info_height)
            start = min(state.info_offset, info_max_offset)
            for offset, line in enumerate(information[start:start + info_height]):
                marker = f"Info {start + offset + 1}/{len(information)} " if offset == 0 else ""
                # The complete value is wrapped before the independent information scroll.
                item_rows.append(_paint(_pad(_fit(marker + line, left_width), left_width), state, color, "meta"))
        while len(item_rows) < body_height:
            item_rows.append(_paint(" " * left_width, state, color))
    detail_rows: list[str] = []
    detail_frame = None
    detail_top = 0
    if show_detail and result.items:
        item = catalog.items[state.selected]
        section_index = min(state.section, len(item.sections) - 1)
        section = item.sections[section_index]
        tab_text = ""
        for index, candidate in enumerate(item.sections):
            text = ("[" + candidate.label + "]" if index == section_index else candidate.label) + " | "
            x = text_cells(tab_text)
            if x < right_width:
                hits.append(BrowserHit("section", index, len(header), left_width + 3 + x if split else x,
                                       min(text_cells(text), right_width - x)))
            tab_text += text
        item_position = result.items.index(state.selected) + 1
        record_position = item.records.index(state.detail.selected_index) + 1 if state.detail.selected_index in item.records else 1
        detail_rows = [_fit(tab_text, right_width),
                       _fit(f"Item {item_position}/{len(result.items)} · Record {record_position}/{len(item.records)} · " + item.label + " · " + (item.display_id or item.id) + " · a Full annotations", right_width)]
        hits.append(BrowserHit("annotations", 0, len(header) + 1, left_width + 3 if split else 0, right_width))
        detail_top = len(detail_rows)
        if section.records:
            detail_frame = render_frame(spec, state.detail, snapshot=snapshot, diagnostic=None, columns=right_width,
                                        rows=max(4, body_height - detail_top), color=color,
                                        session=session, visible_indices=section.records, chrome=False)
            detail_frame = clip_painted_frame(detail_frame, body_height - detail_top)
            detail_rows.extend(detail_frame.text.splitlines())
        else:
            detail_rows.extend(_wrap(section.empty_text, right_width))
    header = [_paint(_pad(line, width), state, color) for line in header]
    body: list[str] = []
    for index in range(body_height):
        left = item_rows[index] if index < len(item_rows) else _paint(" " * left_width, state, color)
        right = _paint(_pad(detail_rows[index] if index < len(detail_rows) else "", right_width), state, color)
        body.append(_pad(left, left_width) + _paint(" │ ", state, color) + right if split else right if show_detail and result.items else left)
    if split or not show_detail:
        for index in range(body_height):
            hits.append(BrowserHit("list_area", -1, len(header) + index, 0, left_width))
    notice = state.error or state.notice or state.detail.message or (catalog.notices[0] if catalog.notices else "")
    selected = selection_label(state.detail.selection, state.detail.selection_count)
    if selected and not state.detail.copy_notice:
        notice = selected
    if state.selected not in visible and result.items and state.list_query:
        notice = "Selected item is outside name results; detail retained. " + notice
    if result.unavailable and not state.detail.copy_notice and not selected:
        notice = f"Search coverage incomplete: {len(result.unavailable)} target(s) · " + result.unavailable[0][1]
    position = _occurrence(state)
    layout = "Split" if state.layout == "split" else "Single"
    if state.layout == "split" and not split:
        layout = "Split→Single"
    status = notice if state.detail.copy_notice or selected else (position + " · " if position else "") + f"Focus: {state.focus.title()} · {notice}"
    if state.panel == "list_find":
        keys = "Name find: ↑/↓ choose · Enter detail · Esc clear · literal text"
    elif state.focus == "list":
        keys = "↑/↓ groups · Enter detail · t name find · ,/. info · Tab focus"
    else:
        keys = "↑/↓ records · Tab list · h/j/k/l cursor · Enter fold · [/] sections"
    footer = [_paint(_pad(_fit(status, width), width), state, color, "meta"),
              _paint(_pad(_fit(layout + " · " + keys + " · v layout · p " + state.appearance.title() + " · f filters · / search · ? help · q close", width), width), state, color)]
    return BrowserFrame("\n".join(header + body + footer), detail_frame,
                        len(header) + detail_top, left_width + 3 if split else 0,
                        tuple(hits), width, height, color, len(header), info_max_offset)
