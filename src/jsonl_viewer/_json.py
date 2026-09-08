"""Strict bounded expansion of complete JSON containers encoded in strings."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from ._model import JSONValue


MAX_EXPANSION_LAYERS = 16
MAX_PROJECTED_DISPLAY_DEPTH = 64
MAX_DERIVED_VALUE_NODES = 200_000
MAX_CUMULATIVE_DECODED_UTF8_BYTES = 16_777_216


@dataclass(slots=True)
class ExpansionBudget:
    """Cumulative work owned by one record traversal, never module state."""

    derived_nodes: int = 0
    decoded_utf8_bytes: int = 0


@dataclass(frozen=True, slots=True)
class Expansion:
    value: JSONValue
    layers: int


@dataclass(frozen=True, slots=True)
class SkippedExpansion:
    reason: str


class _DuplicateObjectField(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateObjectField("duplicate object field")
        value[key] = item
    return value


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _looks_like_nested_json(value: str) -> bool:
    stripped = value.lstrip()
    return bool(stripped) and stripped[0] in {'"', "[", "{"}


def _strict_json_value(value: str) -> JSONValue | None:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (
        _DuplicateObjectField,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        ValueError,
    ):
        return None
    return cast(JSONValue, parsed)


def _candidate_node_count(
    value: list[JSONValue] | dict[str, JSONValue],
    *,
    display_depth: int,
    remaining_nodes: int,
) -> tuple[int | None, str | None]:
    stack: list[tuple[JSONValue, int]] = [(value, display_depth)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        if depth > MAX_PROJECTED_DISPLAY_DEPTH:
            return None, "display depth limit"
        nodes += 1
        if nodes > remaining_nodes:
            return None, "derived node limit"
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return nodes, None


def expand_json_string(
    value: str,
    *,
    display_depth: int,
    budget: ExpansionBudget,
) -> Expansion | SkippedExpansion | None:
    """Return a whole strict container or retain the original string.

    Search and rendering share the same transactional acceptance and record
    budgets. Intermediate scalar/string decodes never become projected leaves.
    """

    if not _looks_like_nested_json(value):
        return None

    current = value
    for layer in range(1, MAX_EXPANSION_LAYERS + 1):
        try:
            encoded_bytes = len(current.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            return None
        if (
            budget.decoded_utf8_bytes + encoded_bytes
            > MAX_CUMULATIVE_DECODED_UTF8_BYTES
        ):
            budget.decoded_utf8_bytes = MAX_CUMULATIVE_DECODED_UTF8_BYTES
            return SkippedExpansion("decoded byte limit")
        parsed = _strict_json_value(current)
        if parsed is None:
            return None
        budget.decoded_utf8_bytes += encoded_bytes
        if isinstance(parsed, (dict, list)):
            node_count, failure = _candidate_node_count(
                parsed,
                display_depth=display_depth,
                remaining_nodes=MAX_DERIVED_VALUE_NODES - budget.derived_nodes,
            )
            if failure is not None:
                if failure == "derived node limit":
                    budget.derived_nodes = MAX_DERIVED_VALUE_NODES
                return SkippedExpansion(failure)
            assert node_count is not None
            budget.derived_nodes += node_count
            return Expansion(parsed, layer)
        if not isinstance(parsed, str):
            return None
        current = parsed
        if not _looks_like_nested_json(current):
            return None

    return SkippedExpansion("encoding layer limit")
