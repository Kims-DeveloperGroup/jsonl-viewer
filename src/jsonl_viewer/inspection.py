"""Immutable, application-independent descriptions of an inspection view.

References are physical, one-based source lines.  No descriptor contains an
executable predicate, loader, validator, or application object.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import math
from typing import TypeAlias


class MissingValue(Enum):
    """A missing property, distinct from JSON null and an empty string."""

    MISSING = "missing"


MISSING = MissingValue.MISSING
Scalar: TypeAlias = str | int | float | bool | None | MissingValue
MAX_DESCRIPTORS = 10_000
MAX_FACETS = 64
MAX_OPTIONS = 256
MAX_METADATA_BYTES = 1_048_576
_MAX_CONTEXT_VALUE_CHARACTERS = 2_048


def _text(value: object, label: str, *, empty: bool = False, maximum: int = 512) -> None:
    if type(value) is not str or (not value and not empty):
        raise TypeError(f"{label} must be text")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its character bound")


def _identifier(value: object) -> None:
    _text(value, "descriptor id", maximum=128)
    if any(ord(character) < 32 for character in value):
        raise ValueError("descriptor id contains a control character")


def _tuple(value: object, expected: type, label: str, maximum: int = MAX_DESCRIPTORS) -> None:
    if type(value) is not tuple or len(value) > maximum:
        raise TypeError(f"{label} must be a bounded tuple")
    if any(type(item) is not expected for item in value):
        raise TypeError(f"{label} contains an unsupported value")


def _reference(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("record references must be positive source lines")


def scalar_key(value: Scalar) -> tuple[type, object]:
    """Preserve JSON scalar types, including bool versus number."""
    if value is MISSING:
        return MissingValue, "missing"
    if value is not None and type(value) not in (str, int, float, bool):
        raise TypeError("facet values must be JSON scalars or MISSING")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("facet numbers must be finite")
    if type(value) is int and value.bit_length() > 16_384:
        raise ValueError("facet integer exceeds its bit bound")
    if type(value) is str and len(value) > 65_536:
        raise ValueError("facet string exceeds its character bound")
    return type(value), value


@dataclass(frozen=True, slots=True)
class Context:
    """A display label (512 characters) and value (2,048 characters)."""

    label: str
    value: str
    short_label: str | None = None

    def __post_init__(self) -> None:
        _text(self.label, "context label")
        _text(self.value, "context value", empty=True, maximum=_MAX_CONTEXT_VALUE_CHARACTERS)
        if self.short_label is not None:
            _text(self.short_label, "short context label")


@dataclass(frozen=True, slots=True)
class Option:
    value: Scalar
    label: str

    def __post_init__(self) -> None:
        scalar_key(self.value)
        _text(self.label, "option label")


@dataclass(frozen=True, slots=True)
class DecodeJson:
    """An explicit bounded JSON-string decoding step, never an expression."""


DECODE = DecodeJson()
PathStep: TypeAlias = str | int | DecodeJson


def _path(value: object) -> None:
    if type(value) is not tuple or len(value) > 64:
        raise TypeError("selector paths must be tuples of at most 64 steps")
    for step in value:
        if type(step) is str:
            _text(step, "path key", empty=True, maximum=65_536)
        elif type(step) is int:
            if step < 0:
                raise ValueError("path indexes must be nonnegative")
        elif type(step) is not DecodeJson:
            raise TypeError("paths accept only keys, indexes, and DecodeJson")


@dataclass(frozen=True, slots=True)
class Target:
    record: int
    path: tuple[PathStep, ...] = ()

    def __post_init__(self) -> None:
        _reference(self.record)
        _path(self.path)


@dataclass(frozen=True, slots=True)
class SearchScope:
    id: str
    label: str
    targets: tuple[Target, ...]
    unavailable: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.id)
        _text(self.label, "scope label")
        _tuple(self.targets, Target, "scope targets")
        _tuple(self.unavailable, tuple, "unavailable targets")
        for row in self.unavailable:
            if len(row) != 2:
                raise ValueError("unavailable targets require a reference and reason")
            _reference(row[0])
            _text(row[1], "unavailable reason")


@dataclass(frozen=True, slots=True)
class Section:
    id: str
    label: str
    records: tuple[int, ...] = ()
    empty_text: str = "No records in this section."

    def __post_init__(self) -> None:
        _identifier(self.id)
        _text(self.label, "section label")
        _tuple(self.records, int, "section references")
        for record in self.records:
            _reference(record)
        if len(set(self.records)) != len(self.records):
            raise ValueError("section references must be unique")
        _text(self.empty_text, "section empty text")


@dataclass(frozen=True, slots=True)
class Group:
    id: str
    label: str
    records: tuple[int, ...]
    sections: tuple[Section, ...] = ()
    values: tuple[tuple[str, Scalar], ...] = ()
    annotations: tuple[Context, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.id)
        _text(self.label, "group label")
        _tuple(self.records, int, "group references")
        if not self.records or len(set(self.records)) != len(self.records):
            raise ValueError("groups require nonempty unique references")
        for record in self.records:
            _reference(record)
        _tuple(self.sections, Section, "group sections", 256)
        _tuple(self.annotations, Context, "group annotations", 64)
        _tuple(self.values, tuple, "group facet values", MAX_FACETS)
        names: set[str] = set()
        for row in self.values:
            if len(row) != 2:
                raise ValueError("group values require a facet id and scalar")
            _identifier(row[0])
            scalar_key(row[1])
            if row[0] in names:
                raise ValueError("group facet values must be unique")
            names.add(row[0])


@dataclass(frozen=True, slots=True)
class Facet:
    id: str
    label: str
    options: tuple[Option, ...] = ()
    path: tuple[str | int, ...] | None = None
    multiple: bool = True

    def __post_init__(self) -> None:
        _identifier(self.id)
        _text(self.label, "facet label")
        _tuple(self.options, Option, "facet options", MAX_OPTIONS)
        keys = [scalar_key(option.value) for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("facet options must have distinct typed values")
        if self.path is not None:
            _path(self.path)
            if any(type(step) is DecodeJson for step in self.path):
                raise ValueError("facet paths do not decode strings")
        if type(self.multiple) is not bool:
            raise TypeError("facet multiple must be bool")


@dataclass(frozen=True, slots=True)
class Inspection:
    groups: tuple[Group, ...] = ()
    facets: tuple[Facet, ...] = ()
    scopes: tuple[SearchScope, ...] = ()

    def __post_init__(self) -> None:
        _tuple(self.groups, Group, "groups")
        _tuple(self.facets, Facet, "facets", MAX_FACETS)
        _tuple(self.scopes, SearchScope, "scopes", 64)
        for collection in (self.groups, self.facets, self.scopes):
            ids = [item.id for item in collection]
            if len(set(ids)) != len(ids):
                raise ValueError("descriptor ids must be unique in their collection")
        if sum(len(group.records) for group in self.groups) > MAX_DESCRIPTORS:
            raise ValueError("group references exceed their bound")
        if sum(len(scope.targets) + len(scope.unavailable) for scope in self.scopes) > MAX_DESCRIPTORS:
            raise ValueError("scope targets exceed their bound")
        # Incremental accounting never expands a shared graph into an enormous
        # repr/JSON string before checking the resource limit.
        size = 0
        stack = [iter((self,))]
        while stack:
            value = next(stack[-1], _END)
            if value is _END:
                stack.pop()
                continue
            size += 16
            if type(value) is str:
                size += len(value.encode("utf-8", errors="backslashreplace"))
            elif type(value) is int:
                size += (value.bit_length() + 7) // 8
            elif type(value) is tuple:
                stack.append(iter(value))
            elif is_dataclass(value):
                stack.append(iter(tuple(getattr(value, f.name) for f in fields(value))))
            if size > MAX_METADATA_BYTES or len(stack) > 128:
                raise ValueError("inspection metadata exceeds its byte bound")


__all__ = [
    "Context", "DecodeJson", "DECODE", "Facet", "Group", "Inspection",
    "MISSING", "MissingValue", "Option", "SearchScope", "Section", "Target",
]

_END = object()
