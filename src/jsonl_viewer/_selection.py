"""Pure projected-text selection ranges and bounded Unicode extraction."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ._cell_layout import RowLayout
from ._model import SelectionPoint, SelectionTextRow, TextSelection, ViewState


def point_key(point: SelectionPoint, records: tuple[int, ...]) -> tuple[int, int, int]:
    return records.index(point.record_index), point.line_index, point.column


def bounds(selection: TextSelection) -> tuple[SelectionPoint, SelectionPoint] | None:
    if not selection.moved:
        return None
    if point_key(selection.anchor, selection.records) < point_key(selection.extent, selection.records):
        return selection.anchor, replace(selection.extent, column=selection.extent.column + selection.extent_width)
    return selection.extent, replace(selection.anchor, column=selection.anchor.column + selection.anchor_width)


def selected_span(selection: TextSelection | None, record: int, line: int,
                  cells: int) -> tuple[int, int] | None:
    limits = bounds(selection) if selection is not None else None
    if limits is None or record not in selection.records:
        return None
    first, last = limits
    order = selection.records.index(record), line
    if not point_key(first, selection.records)[:2] <= order <= point_key(last, selection.records)[:2]:
        return None
    start = first.column if (record, line) == (first.record_index, first.line_index) else 0
    end = last.column if (record, line) == (last.record_index, last.line_index) else cells
    return min(cells, max(0, start)), min(cells, max(0, end))


def selected_text(selection: TextSelection, rows: Iterable[SelectionTextRow]) -> str:
    """Slice only selected projection rows; annotations retain geometry, not text."""
    if bounds(selection) is None:
        return ""
    lines: list[str] = []
    previous_record = None
    for row in rows:
        if not any(copyable for _, copyable in row.parts):
            continue
        layout = RowLayout.build(text for text, _ in row.parts)
        span = selected_span(selection, row.record_index, row.line_index, layout.cells)
        if span is None:
            continue
        start, end = span
        pieces: list[str] = []
        for index, column in enumerate(layout.starts):
            text, copyable = row.parts[index]
            if not copyable:
                continue
            part = layout.parts[index]
            if start <= column and column + part.cells <= end:
                pieces.append(text)
            else:
                pieces.extend(text[begin:finish] for _, begin, finish, _ in
                              part.window(start - column, end - column))
        if previous_record is not None and previous_record != row.record_index:
            lines.append("")
        lines.append("".join(pieces))
        previous_record = row.record_index
    return "\n".join(lines)


def selected_row_length(parts: tuple[tuple[str, bool], ...], layout: RowLayout,
                        start: int, end: int) -> int:
    """Count via sparse endpoint seeks, without visiting the intervening text."""
    total = 0
    for index, column in enumerate(layout.starts):
        text, copyable = parts[index]
        if not copyable:
            continue
        part = layout.parts[index]

        def offset(cell: int) -> int:
            if cell <= 0:
                return 0
            if cell >= part.cells:
                return len(text)
            nearest = part.nearest(cell)
            return nearest[1] if nearest is not None else 0

        total += max(0, offset(end - column) - offset(start - column))
    return total


def selection_label(selection: TextSelection | None, count: int | None = None) -> str:
    if selection is None or bounds(selection) is None:
        return ""
    first, last = bounds(selection)
    action = "Selecting" if selection.dragging else "Selected"
    amount = f" {count} characters ·" if count is not None else ""
    return (f"{action}{amount} R{first.record_index + 1} L{first.line_index + 1}:{first.column + 1}"
            f"–R{last.record_index + 1} L{last.line_index + 1}:{last.column}"
            + (" · release to copy" if selection.dragging else ""))


def clear_selection(state: ViewState) -> ViewState:
    if state.selection is None and not state.copy_pending and not state.copy_notice:
        return state
    return replace(state, selection=None, copy_pending=False, copy_notice=False, selection_count=0,
                   message=None if state.copy_notice else state.message,
                   message_is_error=False if state.copy_notice else state.message_is_error)
