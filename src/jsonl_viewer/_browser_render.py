"""Deterministic generic list, modal, and detail-pane presentation."""

from __future__ import annotations

from dataclasses import replace

from ._catalog import Catalog
from ._cell_layout import clusters, text_cells
from ._model import BrowserFrame, BrowserHit, BrowserState, Snapshot
from ._render import RenderSession, _clip_text, _neutralize_text, render_frame, strip_ansi
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
            "Enter: open detail or fold JSON", "h/j/k/l: JSON cursor; J/K: sibling keys",
            "[/]: previous/next detail section", "/: edit literal search and scope",
            "f: filter draft; Apply commits all changes", "n/N: next/previous occurrence",
            "c: clear query, retain filters and scope", "g: original source line",
            "a: full item annotations", "m: Simple/Verbose; Left/Right: pan",
            "Esc: cancel modal/search/notice; q: close from main view",
        )]
        explanation = "Source records remain immutable."
        help_text = "↑/↓ scroll · Esc return"
    else:
        title = "Item annotations"
        item = catalog.items[state.selected]
        rows = ["Identity: " + item.id, "Label: " + item.label]
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


def render_browser(spec: ViewerSpec, state: BrowserState, snapshot: Snapshot,
                   catalog: Catalog, *, columns: int, rows: int, color: bool,
                   session: RenderSession) -> BrowserFrame:
    width, height = min(240, max(1, columns)), min(100, max(1, rows))
    if width < 24 or height < 6:
        lines = [_fit(spec.title + " · READ ONLY", width), _fit("Resize terminal to inspect data.", width)]
        return BrowserFrame("\n".join(lines[:height]), columns=width, rows=height, color=color)
    if state.panel:
        return replace(_modal(spec, catalog, state, width, height), color=color)
    result = state.result
    assert result is not None
    scope = _scope(catalog, state.scope)
    active = [(facet.label, _selection(catalog, state.filters, i))
              for i, facet in enumerate(catalog.facets) if state.filters[i]]
    filters = " | ".join(f"{name}={value}" for name, value in active) or "All"
    unit = "Groups/records" if any(i.grouped for i in catalog.items) else "Records"
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
    for kind, row in (("search", 1 + bool(context)), ("filters", 2 + bool(context))):
        if row < len(header):
            hits.append(BrowserHit(kind, 0, row, 0, width))
    body_height = max(1, height - len(header) - 2)
    split = width >= 120 and height >= 12
    show_detail = split or state.focus == "detail"
    left_width = min(48, width // 3) if split else width
    right_width = width - left_width - 3 if split else width
    item_rows: list[str] = []
    if state.diagnostic is not None:
        item_rows = _wrap("Input error: " + state.diagnostic.summary, left_width)
        item_rows.extend(_wrap(state.diagnostic.detail, left_width))
    elif not result.items:
        message = "No recorded data" if not snapshot.records else "No items match the filters" if not result.filtered else "No text matches"
        item_rows = [_fit(message, left_width), _fit("f Edit filters · / Edit search · c Clear search", left_width)]
    else:
        position = result.items.index(state.selected) if state.selected in result.items else 0
        capacity = max(1, body_height // 2)
        start = max(0, min(position - capacity + 1, len(result.items) - capacity))
        for item_index in result.items[start:start + capacity]:
            item = catalog.items[item_index]
            marker = "> " if item_index == state.selected else "  "
            row = len(header) + len(item_rows)
            if split or not show_detail:
                hits.append(BrowserHit("item", item_index, row, 0, left_width))
            item_rows.append(_fit(marker + item.label, left_width))
            excerpt = ""
            hit = next((h for h in result.search.occurrences if h.record_index in item.records), None) if result.search else None
            if hit:
                excerpt = (f"R{snapshot.records[hit.record_index].source_line} {hit.path}: "
                           + hit.text[max(0, hit.start - 20):hit.start] + "⟦"
                           + hit.text[hit.start:hit.end] + "⟧" + hit.text[hit.end:hit.end + 20])
            else:
                excerpt = f"{len(item.records)} record(s) · R{snapshot.records[item.records[0]].source_line}"
            if split or not show_detail:
                hits.append(BrowserHit("item", item_index, row + 1, 0, left_width))
            item_rows.append(_fit("  " + excerpt, left_width))
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
        detail_rows = [_fit(tab_text, right_width), _fit(item.id + " · a annotations", right_width)]
        hits.append(BrowserHit("annotations", 0, len(header) + 1, left_width + 3 if split else 0, right_width))
        detail_top = len(detail_rows)
        if section.records:
            detail_frame = render_frame(spec, state.detail, snapshot=snapshot, diagnostic=None,
                                        columns=right_width, rows=max(4, body_height - detail_top),
                                        color=color, session=session,
                                        visible_indices=section.records, chrome=False)
            detail_rows.extend(detail_frame.text.splitlines())
        else:
            detail_rows.extend(_wrap(section.empty_text, right_width))
    body: list[str] = []
    for row in range(body_height):
        left = item_rows[row] if row < len(item_rows) else ""
        right = detail_rows[row] if row < len(detail_rows) else ""
        if split:
            body.append(_pad(left, left_width) + " │ " + right)
        else:
            body.append(right if show_detail and result.items else left)
    notice = state.error or state.notice or state.detail.message or (catalog.notices[0] if catalog.notices else "")
    if result.unavailable:
        notice = f"Search coverage incomplete: {len(result.unavailable)} target(s) · " + result.unavailable[0][1]
    position = _occurrence(state)
    footer = [_fit((position + " · " if position else "") + f"Focus: {state.focus.title()} · {notice}", width),
              _fit("Tab focus · ↑/↓ items/records · n/N hits · [/] sections · f filters · / search · ? help · q close", width)]
    return BrowserFrame("\n".join(header + body + footer), detail_frame,
                        len(header) + detail_top, left_width + 3 if split else 0, tuple(hits),
                        width, height, color, len(header))
