"""Pure literal selectors and full-value substring matching for JSON records."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ._json import Expansion, ExpansionBudget, expand_json_string
from ._model import JSONPath, JSONValue, MatchPaths, Snapshot


MAX_FIELD_CHARACTERS = 128


class SelectorError(ValueError):
    """A bounded, correctable selector error without source content."""


@dataclass(frozen=True, slots=True)
class Selector:
    field: str
    parts: JSONPath | None
    pointer: bool = False


def _pointer_parts(field: str) -> JSONPath:
    parts: list[str] = []
    for segment in field[1:].split("/"):
        decoded: list[str] = []
        position = 0
        while position < len(segment):
            character = segment[position]
            if character == "~":
                position += 1
                if position == len(segment) or segment[position] not in "01":
                    raise SelectorError("JSON Pointer escapes must be ~0 or ~1.")
                character = "~" if segment[position] == "0" else "/"
            decoded.append(character)
            position += 1
        parts.append("".join(decoded))
    return tuple(parts)


def _array_index(value: str) -> int | None:
    if not value or any(character not in "0123456789" for character in value):
        return None
    if len(value) > 1 and value[0] == "0":
        return None
    return int(value, 10)


def _dotted_parts(field: str) -> JSONPath:
    parts: list[str | int] = []
    position = 0
    error = "Malformed field path; use dotted keys, [INDEX], or JSON Pointer."
    while position < len(field):
        if position != 0 or field[position] != "[":
            start = position
            while position < len(field) and field[position] not in ".[]":
                position += 1
            if start == position:
                raise SelectorError(error)
            parts.append(field[start:position])
        while position < len(field) and field[position] == "[":
            end = field.find("]", position + 1)
            if end == -1:
                raise SelectorError(error)
            index = _array_index(field[position + 1:end])
            if index is None:
                raise SelectorError("Array selectors require a nonnegative integer index.")
            parts.append(index)
            position = end + 1
        if position == len(field):
            break
        if field[position] != "." or position + 1 == len(field):
            raise SelectorError(error)
        position += 1
    return tuple(parts)


def parse_selector(field: str, snapshot: Snapshot) -> Selector:
    """Validate a selector, retaining exact nonempty top-level key priority.

    A literal key can shadow even Pointer syntax or a malformed path. Missing
    literal keys fall through to traversal; blank field always means full text.
    """

    if len(field) > MAX_FIELD_CHARACTERS:
        raise SelectorError("Search field is too long (128 characters maximum).")
    if any(character in field for character in ("\x00", "\n", "\r", "\t")):
        raise SelectorError("Search field contains a control separator.")
    if not field:
        return Selector(field, ())
    try:
        parts = _pointer_parts(field) if field.startswith("/") else _dotted_parts(field)
    except SelectorError:
        if any(
            isinstance(record.value, dict) and field in record.value
            for record in snapshot.records
        ):
            return Selector(field, None)
        raise
    return Selector(field, parts, pointer=field.startswith("/"))


def _resolve(
    value: JSONValue,
    selector: Selector,
    budget: ExpansionBudget,
) -> tuple[JSONValue, JSONPath] | None:
    if isinstance(value, dict) and selector.field in value:
        return value[selector.field], (selector.field,)
    if selector.parts is None:
        return None
    path: JSONPath = ()
    for part in selector.parts:
        if isinstance(value, str):
            expansion = expand_json_string(value, display_depth=len(path) + 1, budget=budget)
            if not isinstance(expansion, Expansion):
                return None
            value = expansion.value
        if isinstance(value, dict) and isinstance(part, str) and part in value:
            value = value[part]
        elif isinstance(value, list):
            index = _array_index(part) if selector.pointer and isinstance(part, str) else part
            if not isinstance(index, int) or index >= len(value):
                return None
            part = index
            value = value[index]
        else:
            return None
        path += (part,)
    return value, path


def _value_text(value: JSONValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _collect_paths(
    value: JSONValue,
    path: JSONPath,
    folded: str,
    budget: ExpansionBudget,
    paths: list[JSONPath],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if folded in key.casefold():
                paths.append(child_path)
            _collect_paths(child, child_path, folded, budget, paths)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_paths(child, path + (index,), folded, budget, paths)
    elif isinstance(value, str):
        start = len(paths)
        expansion = expand_json_string(value, display_depth=len(path) + 1, budget=budget)
        if isinstance(expansion, Expansion):
            _collect_paths(expansion.value, path, folded, budget, paths)
            if len(paths) == start and folded in _value_text(expansion.value).casefold():
                paths.append(path)
        # Prefer resolved children when the same text also occurs in their
        # encoding; a literal-only hit still identifies the original string.
        if len(paths) == start and folded in value.casefold():
            paths.append(path)
    elif folded in _value_text(value).casefold():
        paths.append(path)


def _matching_paths(
    value: JSONValue,
    path: JSONPath,
    folded: str,
    budget: ExpansionBudget,
    *,
    whole_record: bool,
) -> MatchPaths:
    if isinstance(value, str) and not whole_record:
        # Selecting a string itself keeps the legacy original-literal search;
        # only deeper selectors or full text interpret its encoded children.
        return (path,) if folded in value.casefold() else ()
    paths: list[JSONPath] = []
    _collect_paths(value, path, folded, budget, paths)
    if not paths and folded in _value_text(value).casefold():
        paths.append(path)
    return tuple(dict.fromkeys(paths))


def find_matches(
    snapshot: Snapshot,
    selector: Selector,
    query: str,
) -> tuple[tuple[int, ...], tuple[MatchPaths, ...]]:
    """Search full bounded values, returning one match and paths per record."""

    folded = query.casefold()
    matches: list[int] = []
    match_paths: list[MatchPaths] = []
    for index, record in enumerate(snapshot.records):
        budget = ExpansionBudget()
        selected = (
            (record.value, ()) if not selector.field
            else _resolve(record.value, selector, budget)
        )
        if selected is None:
            continue
        value, path = selected
        paths = _matching_paths(
            value, path, folded, budget, whole_record=not selector.field,
        )
        if paths:
            matches.append(index)
            match_paths.append(paths)
    return tuple(matches), tuple(match_paths)
