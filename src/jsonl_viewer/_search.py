"""Pure bounded full-text occurrence matching over immutable JSON values."""

from __future__ import annotations

import json
from array import array

from ._json import Expansion, ExpansionBudget, SkippedExpansion, expand_json_string
from ._model import JSONPath, JSONValue, Occurrence, Snapshot
from .inspection import DecodeJson, PathStep, Target

MAX_OCCURRENCES = 100_000


class _ScopeNode:
    """One node in a bounded, typed selector trie."""

    __slots__ = ("selected", "children")

    def __init__(self) -> None:
        self.selected = False
        self.children: dict[PathStep, _ScopeNode] = {}


class _ScopedRecordSearch:
    """One original record's shared expansion accounting and source traversal."""

    def __init__(self, record: int, query: str, hits: list[Occurrence],
                 seen: set[tuple], selected: set[tuple[PathStep, ...]],
                 decode_paths: set[tuple[PathStep, ...]]) -> None:
        self.record = record
        self.query = query.casefold()
        self.hits = hits
        self.seen = seen
        self.budget = ExpansionBudget()
        self.selected = selected
        self.decode_paths = decode_paths
        self.decoded: dict[tuple[PathStep, ...], Expansion | SkippedExpansion | None] = {}
        self.searched: dict[tuple[PathStep, ...], bool] = {}
        self.unavailable: set[str] = set()

    def _add(self, text: str, path: JSONPath, kind: str) -> bool:
        found = False
        for start, end in _spans(text, self.query):
            found = True
            key = (self.record, path, kind, start, end)
            if key in self.seen:
                continue
            if len(self.hits) >= MAX_OCCURRENCES:
                raise SearchLimitError("Too many occurrences; refine the search.")
            self.seen.add(key)
            self.hits.append(Occurrence(self.record, path, kind, start, end, text))
        return found

    def _decode(self, value: JSONValue, prefix: tuple[PathStep, ...], depth: int):
        if prefix in self.decoded:
            return self.decoded[prefix]
        result = expand_json_string(value, display_depth=depth, budget=self.budget) if isinstance(value, str) else None
        # Only selector-addressable decodes need retention. All other decoded
        # subtrees are traversed once under their selected ancestor and released.
        if prefix in self.decode_paths:
            self.decoded[prefix] = result
        if isinstance(result, SkippedExpansion):
            self.unavailable.add("JSON decoding bounded: " + result.reason)
        return result

    def walk(self, value: JSONValue, path: JSONPath,
             prefix: tuple[PathStep, ...], depth: int) -> bool:
        if prefix in self.searched:
            return self.searched[prefix]
        found = False
        if isinstance(value, dict):
            for key, child in value.items():
                key_hit = self._add(key, path + (key,), "key")
                child_hit = self.walk(child, path + (key,), prefix + (key,), depth + 1)
                found = key_hit or child_hit or found
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_hit = self.walk(child, path + (index,), prefix + (index,), depth + 1)
                found = child_hit or found
        elif isinstance(value, str):
            decoded_prefix = prefix + (DecodeJson(),)
            expanded = self._decode(value, decoded_prefix, depth)
            if isinstance(expanded, Expansion):
                found = self.walk(expanded.value, path, decoded_prefix, depth)
                if not found:
                    found = self._add(value, path, "raw")
            else:
                found = self._add(value, path, "value")
        else:
            found = self._add(_value_text(value), path, "value")
        if isinstance(value, (dict, list)) and not found:
            found = self._add(_value_text(value), path, "normalized")
        if prefix in self.selected:
            self.searched[prefix] = found
        return found

    def visit(self, value: JSONValue, path: JSONPath, prefix: tuple[PathStep, ...],
              depth: int, node: _ScopeNode) -> None:
        if node.selected:
            self.walk(value, path, prefix, depth)
        pending = dict(node.children)
        # Follow the original container's insertion order rather than descriptor
        # order. DecodeJson is an explicit representation edge, not a JSON key.
        if isinstance(value, dict):
            for key, child in value.items():
                branch = pending.pop(key, None)
                if branch is not None:
                    self.visit(child, path + (key,), prefix + (key,), depth + 1, branch)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                branch = pending.pop(index, None)
                if branch is not None:
                    self.visit(child, path + (index,), prefix + (index,), depth + 1, branch)
        decode_step = DecodeJson()
        branch = pending.pop(decode_step, None)
        if branch is not None:
            decoded_prefix = prefix + (decode_step,)
            expanded = self._decode(value, decoded_prefix, depth)
            if isinstance(expanded, Expansion):
                self.visit(expanded.value, path, decoded_prefix, depth, branch)
            else:
                self.unavailable.add("Selected JSON decoding is unavailable")
        if pending:
            self.unavailable.add("Selected path is unavailable")


def find_scoped_matches(snapshot: Snapshot, query: str, targets: tuple[Target, ...],
                        eligible: set[int]) -> tuple[tuple[Occurrence, ...], tuple[tuple[int, str], ...]]:
    """Search typed targets with one deterministic budget per original record."""
    if not query or len(query) > 1_024 or not all(character.isprintable() for character in query):
        raise ValueError("Search requires 1–1,024 printable characters.")
    references = {record.source_line: index for index, record in enumerate(snapshot.records)}
    tries: dict[int, _ScopeNode] = {}
    selected: dict[int, set[tuple[PathStep, ...]]] = {}
    decodes: dict[int, set[tuple[PathStep, ...]]] = {}
    for target in targets:
        if target.record not in references:
            raise ValueError("scope contains an unknown record reference")
        index = references[target.record]
        if index not in eligible:
            continue
        node = tries.setdefault(index, _ScopeNode())
        selected.setdefault(index, set()).add(target.path)
        decode_paths = decodes.setdefault(index, set())
        for offset, step in enumerate(target.path):
            node = node.children.setdefault(step, _ScopeNode())
            if type(step) is DecodeJson:
                decode_paths.add(target.path[:offset + 1])
        node.selected = True
    hits: list[Occurrence] = []
    seen: set[tuple] = set()
    unavailable: list[tuple[int, str]] = []
    for index in sorted(tries):
        search = _ScopedRecordSearch(index, query, hits, seen, selected[index], decodes[index])
        search.visit(snapshot.records[index].value, (), (), 1, tries[index])
        unavailable.extend((index, reason) for reason in sorted(search.unavailable))
    return tuple(hits), tuple(unavailable)


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
