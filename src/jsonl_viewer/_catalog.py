"""Bind inert inspection descriptions to original snapshot references."""

from __future__ import annotations

from dataclasses import dataclass
import json

from ._json import Expansion, ExpansionBudget, expand_json_string
from ._model import JSONPath, JSONValue, Snapshot
from .inspection import (
    Context, DecodeJson, Facet, Group, Inspection, MISSING, MAX_FACETS,
    MAX_OPTIONS, Option, Scalar, scalar_key,
)


@dataclass(frozen=True, slots=True)
class BoundSection:
    id: str
    label: str
    records: tuple[int, ...]
    empty_text: str = "No records in this section."


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    label: str
    records: tuple[int, ...]
    sections: tuple[BoundSection, ...]
    values: tuple[Scalar, ...] = ()
    annotations: tuple[Context, ...] = ()
    grouped: bool = False


@dataclass(frozen=True, slots=True)
class BoundScope:
    id: str
    label: str
    roots: tuple[tuple[int, JSONPath, JSONValue], ...]
    unavailable: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Catalog:
    items: tuple[Item, ...]
    facets: tuple[Facet, ...]
    scopes: tuple[BoundScope, ...]
    record_items: tuple[int, ...]
    notices: tuple[str, ...] = ()


def _path_value(value: JSONValue, path: tuple[str | int, ...]) -> Scalar:
    current = value
    for step in path:
        if type(step) is str and isinstance(current, dict) and step in current:
            current = current[step]
        elif type(step) is int and isinstance(current, list) and step < len(current):
            current = current[step]
        else:
            return MISSING
    if isinstance(current, (dict, list)):
        raise ValueError("facet path selects a non-scalar value")
    scalar_key(current)
    return current


def _label(value: Scalar) -> str:
    if value is MISSING:
        return "Missing"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 512 else text[:511] + "…"


def _automatic_facets(snapshot: Snapshot) -> tuple[tuple[Facet, ...], tuple[str, ...]]:
    keys: dict[str, None] = {}
    omitted = False
    for record in snapshot.records:
        if isinstance(record.value, dict):
            for key in record.value:
                if len(keys) < MAX_FACETS:
                    keys.setdefault(key, None)
                elif key not in keys:
                    omitted = True
    paths = [(key, (key,)) for key in keys]
    if snapshot.records and all(not isinstance(r.value, (dict, list)) for r in snapshot.records):
        paths = [("Value", ())]
    result: list[Facet] = []
    for number, (label, path) in enumerate(paths):
        if len(label) > 512:
            omitted = True
            continue
        try:
            values = {_typed: value for record in snapshot.records
                      for value in (_path_value(record.value, path),)
                      for _typed in (scalar_key(value),)}
        except ValueError:
            omitted = True
            continue
        if len(values) <= MAX_OPTIONS:
            result.append(Facet(f"field-{number}", label,
                                tuple(Option(v, _label(v)) for v in values.values()), path))
        else:
            omitted = True
    notices = ("Some fields are unavailable as filters (type or discovery limits).",) if omitted else ()
    return tuple(result), notices


def bind_catalog(snapshot: Snapshot, description: Inspection | None) -> Catalog:
    description = description or Inspection()
    refs = {record.source_line: index for index, record in enumerate(snapshot.records)}
    occupied: set[int] = set()
    grouped: dict[int, Group] = {}
    for group in description.groups:
        for reference in group.records:
            if reference not in refs:
                raise ValueError("group contains an unknown record reference")
            if reference in occupied:
                raise ValueError("record belongs to more than one group")
            occupied.add(reference)
        grouped[min(refs[ref] for ref in group.records)] = group
        section_ids: set[str] = set()
        for section in group.sections:
            if section.id in section_ids:
                raise ValueError("section ids must be unique within a group")
            section_ids.add(section.id)
            if not set(section.records).issubset(group.records):
                raise ValueError("section references must belong to its group")
    if description.facets:
        facets, notices = description.facets, ()
    else:
        facets, notices = _automatic_facets(snapshot)
    items: list[Item] = []
    for index, record in enumerate(snapshot.records):
        group = grouped.get(index)
        if group is None and record.source_line in occupied:
            continue
        if group is not None:
            members = tuple(sorted(refs[ref] for ref in group.records))
            sections = tuple(BoundSection(s.id, s.label, tuple(sorted(refs[r] for r in s.records)),
                                          s.empty_text) for s in group.sections)
            covered = {r for section in sections for r in section.records}
            remainder = tuple(r for r in members if r not in covered)
            if not sections:
                sections = (BoundSection("", "Records", members),)
            elif remainder:
                sections += (BoundSection("\x00", "Other records", remainder),)
            supplied = dict(group.values)
            values = tuple(supplied.get(f.id, MISSING) for f in facets)
            items.append(Item("group:" + group.id, group.label, members, sections,
                              values, group.annotations, True))
        else:
            values = tuple(_path_value(record.value, f.path) if f.path is not None else MISSING
                           for f in facets)
            items.append(Item(f"record:{record.source_line}", f"Record {record.source_line}",
                              (index,), (BoundSection("", "Record", (index,)),), values))
    completed_facets: list[Facet] = []
    for f_index, facet in enumerate(facets):
        options = facet.options
        if not options:
            seen: dict[tuple[type, object], Scalar] = {}
            for item in items:
                value = item.values[f_index]
                seen.setdefault(scalar_key(value), value)
            if len(seen) > MAX_OPTIONS:
                raise ValueError("observed facet options exceed their bound")
            options = tuple(Option(value, _label(value)) for value in seen.values())
        completed_facets.append(Facet(facet.id, facet.label, options, facet.path, facet.multiple))
    record_items = [0] * len(snapshot.records)
    for item_index, item in enumerate(items):
        for member in item.records:
            record_items[member] = item_index
    scopes: list[BoundScope] = []
    budgets: dict[int, ExpansionBudget] = {}
    resolved: dict[tuple[int, tuple], tuple[JSONPath, JSONValue] | str] = {}
    for scope in description.scopes:
        roots: list[tuple[int, JSONPath, JSONValue]] = []
        unavailable: list[tuple[int, str]] = []
        root_ids: set[tuple[int, tuple]] = set()
        for ref, reason in scope.unavailable:
            if ref not in refs:
                raise ValueError("scope contains an unknown record reference")
            unavailable.append((refs[ref], reason))
        for target in scope.targets:
            if target.record not in refs:
                raise ValueError("scope contains an unknown record reference")
            index = refs[target.record]
            value = snapshot.records[index].value
            path: list[str | int] = []
            budget = budgets.setdefault(index, ExpansionBudget())
            failed: str | None = None
            for position, step in enumerate(target.path):
                cache_key = (index, target.path[:position + 1])
                if cache_key in resolved:
                    cached = resolved[cache_key]
                    if type(cached) is str:
                        failed = cached
                        break
                    cached_path, value = cached
                    path = list(cached_path)
                    continue
                if type(step) is DecodeJson:
                    expansion = expand_json_string(value, display_depth=len(path) + 1,
                                                   budget=budget) if isinstance(value, str) else None
                    if not isinstance(expansion, Expansion):
                        failed = "JSON decoding unavailable or bounded"
                        resolved[cache_key] = failed
                        break
                    value = expansion.value
                elif type(step) is str and isinstance(value, dict) and step in value:
                    path.append(step)
                    value = value[step]
                elif type(step) is int and isinstance(value, list) and step < len(value):
                    path.append(step)
                    value = value[step]
                else:
                    failed = "Selected path is unavailable"
                    resolved[cache_key] = failed
                    break
                resolved[cache_key] = (tuple(path), value)
            if failed:
                unavailable.append((index, failed))
            elif (index, target.path) not in root_ids:
                roots.append((index, tuple(path), value))
                root_ids.add((index, target.path))
        scopes.append(BoundScope(scope.id, scope.label, tuple(roots), tuple(unavailable)))
    return Catalog(tuple(items), tuple(completed_facets), tuple(scopes), tuple(record_items), notices)
