"""Pure bounded full-text occurrence matching over immutable JSON values."""

from __future__ import annotations

import json
from array import array

from ._json import Expansion, ExpansionBudget, expand_json_string
from ._model import JSONPath, JSONValue, Occurrence, Snapshot

MAX_OCCURRENCES = 100_000


class SearchLimitError(ValueError):
    """A transactional search failure: callers retain the committed search."""


def _value_text(value: JSONValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _spans(text: str, folded_query: str):
    """Left-to-right nonoverlapping folded matches mapped to original spans.

    Fold expansions map back to original code points (both s's in ß identify
    one character). Duplicate and overlapping original spans are suppressed.
    """
    folded = text.casefold()
    if not folded_query or folded_query not in folded:
        return
    offsets = array("I")
    for index, character in enumerate(text):
        offsets.extend([index] * len(character.casefold()))
    position = 0
    previous_end = -1
    while folded_query and (position := folded.find(folded_query, position)) >= 0:
        end = position + len(folded_query)
        original_start, original_end = offsets[position], offsets[end - 1] + 1
        if original_start >= previous_end:
            yield original_start, original_end
            previous_end = original_end
        position = end


def find_matches(snapshot: Snapshot, query: str) -> tuple[Occurrence, ...]:
    """Visit source records, insertion-order keys/children, then leaf offsets.

    Decoded children take precedence over their raw encoding. Normalized or
    raw fallback text is searched only when the corresponding subtree has no
    direct hits, preventing duplicate representations of the same match.
    """
    folded = query.casefold()
    hits: list[Occurrence] = []

    def add(text: str, record: int, path: JSONPath, kind: str) -> None:
        for start, end in _spans(text, folded):
            if len(hits) == MAX_OCCURRENCES:
                raise SearchLimitError("More than 100000 occurrences; refine the query.")
            hits.append(Occurrence(record, path, kind, start, end, text))

    def walk(value: JSONValue, record: int, path: JSONPath,
             budget: ExpansionBudget) -> None:
        initial = len(hits)
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + (key,)
                add(key, record, child_path, "key")
                walk(child, record, child_path, budget)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, record, path + (index,), budget)
        elif isinstance(value, str):
            expanded = expand_json_string(value, display_depth=len(path) + 1, budget=budget)
            if isinstance(expanded, Expansion):
                walk(expanded.value, record, path, budget)
                if len(hits) == initial:
                    add(value, record, path, "raw")
            else:
                add(value, record, path, "value")
        else:
            add(_value_text(value), record, path, "value")
        if isinstance(value, (dict, list)) and len(hits) == initial:
            add(_value_text(value), record, path, "normalized")

    for index, record in enumerate(snapshot.records):
        walk(record.value, index, (), ExpansionBudget())
    return tuple(hits)
