"""Pure typed facet selection and source-referenced literal search results."""

from __future__ import annotations

from dataclasses import replace

from ._catalog import Catalog
from ._model import JSONPath, JSONValue, Occurrence, QueryResult, Record, SearchState, Snapshot
from ._search import MAX_OCCURRENCES, SearchLimitError, find_matches, find_scoped_matches
from .inspection import scalar_key




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
        scoped, current_unavailable = find_scoped_matches(snapshot, query, scope.targets, eligible)
        occurrences.extend(scoped)
        declared = tuple(row for row in scope.declared_unavailable if row[0] in eligible)
        unavailable = tuple(dict.fromkeys(declared + current_unavailable))
    search = SearchState(query, tuple(occurrences), 0 if occurrences else None)
    matching = {catalog.record_items[hit.record_index] for hit in occurrences}
    result_items = tuple(index for index in filtered if index in matching)
    return QueryResult(tuple(filtered), result_items, search,
                       len({hit.record_index for hit in occurrences}), unavailable)
