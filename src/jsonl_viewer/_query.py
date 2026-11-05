"""Pure typed facet selection and source-referenced literal search results."""

from __future__ import annotations

from dataclasses import replace

from ._catalog import Catalog
from ._json import Expansion, ExpansionBudget, expand_json_string
from ._model import JSONPath, JSONValue, Occurrence, QueryResult, Record, SearchState, Snapshot
from ._search import MAX_OCCURRENCES, SearchLimitError, find_matches
from .inspection import scalar_key


def _path_ranks(value: JSONValue) -> dict[JSONPath, int]:
    ranks: dict[JSONPath, int] = {}
    budget = ExpansionBudget()

    def visit(current: JSONValue, path: JSONPath) -> None:
        ranks.setdefault(path, len(ranks))
        if isinstance(current, dict):
            for key, child in current.items():
                visit(child, path + (key,))
        elif isinstance(current, list):
            for index, child in enumerate(current):
                visit(child, path + (index,))
        elif isinstance(current, str):
            expanded = expand_json_string(current, display_depth=len(path) + 1, budget=budget)
            if isinstance(expanded, Expansion):
                visit(expanded.value, path)

    visit(value, ())
    return ranks


def evaluate(
    snapshot: Snapshot,
    catalog: Catalog,
    selections: tuple[frozenset[int], ...],
    query: str | None,
    scope_index: int = -1,
) -> QueryResult:
    """Calculate a complete candidate result without changing committed state."""
    if len(selections) != len(catalog.facets):
        raise ValueError("filter selections do not match their facets")
    for facet, selected in zip(catalog.facets, selections):
        if any(type(option) is not int or option < 0 or option >= len(facet.options)
               for option in selected):
            raise ValueError("filter option is unavailable")
        if not facet.multiple and len(selected) > 1:
            raise ValueError("single-select facet has multiple options")
    if scope_index < -1 or scope_index >= len(catalog.scopes):
        raise ValueError("search scope is unavailable")
    filtered: list[int] = []
    wanted = tuple(frozenset(scalar_key(facet.options[o].value) for o in selected)
                   for facet, selected in zip(catalog.facets, selections))
    for index, item in enumerate(catalog.items):
        accepted = True
        for f_index, selected in enumerate(wanted):
            if selected and scalar_key(item.values[f_index]) not in selected:
                accepted = False
                break
        if accepted:
            filtered.append(index)
    if query is None:
        return QueryResult(tuple(filtered), tuple(filtered), None)
    if not query or len(query) > 1_024 or not all(c.isprintable() for c in query):
        raise ValueError("Search requires 1–1,024 printable characters.")
    eligible = {record for index in filtered for record in catalog.items[index].records}
    occurrences: list[Occurrence] = []
    seen: set[tuple[object, ...]] = set()

    def add(index: int, path: JSONPath, value: JSONValue) -> None:
        # Search a value projection, never a serialized substitute source.
        # Every returned occurrence is immediately rebound to its original
        # snapshot index and structural path before UI state can observe it.
        source = snapshot.records[index]
        projection = Snapshot((Record(source.source_line, source.utf8_bytes, value),),
                              snapshot.source_utf8_bytes)
        for hit in find_matches(projection, query):
            mapped = replace(hit, record_index=index, path=path + hit.path)
            identity = (index, mapped.path, mapped.kind, mapped.start, mapped.end)
            if identity in seen:
                continue
            if len(occurrences) >= MAX_OCCURRENCES:
                raise SearchLimitError("Too many occurrences; refine the search.")
            seen.add(identity)
            occurrences.append(mapped)

    unavailable: tuple[tuple[int, str], ...] = ()
    if scope_index < 0:
        for index in sorted(eligible):
            add(index, (), snapshot.records[index].value)
    else:
        scope = catalog.scopes[scope_index]
        covered: dict[int, set[JSONPath]] = {}
        for index, path, value in sorted(scope.roots, key=lambda row: (row[0], len(row[1]))):
            if index in eligible:
                paths = covered.setdefault(index, set())
                if any(path[:length] in paths for length in range(len(path) + 1)):
                    continue
                paths.add(path)
                add(index, path, value)
        unavailable = tuple(dict.fromkeys(row for row in scope.unavailable if row[0] in eligible))
        ranks = {index: _path_ranks(snapshot.records[index].value)
                 for index in {hit.record_index for hit in occurrences}}
        occurrences.sort(key=lambda hit: (
            hit.record_index, ranks[hit.record_index].get(hit.path, 0),
            0 if hit.kind == "key" else 1, hit.start,
        ))
    search = SearchState(query, tuple(occurrences), 0 if occurrences else None)
    matching = {catalog.record_items[hit.record_index] for hit in occurrences}
    result_items = tuple(index for index in filtered if index in matching)
    return QueryResult(tuple(filtered), result_items, search,
                       len({hit.record_index for hit in occurrences}), unavailable)
