"""Strict bounded parsing of one immutable UTF-8 JSONL snapshot."""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn, cast

from ._model import InputDiagnostic, JSONValue, Record, Snapshot


MAX_SOURCE_BYTES = 16_777_216
MAX_RECORD_BYTES = 2_097_152
MAX_RECORDS = 10_000
MAX_NESTING_DEPTH = 64
MAX_VALUE_NODES = 200_000


class InputFailure(Exception):
    """Carry one content-safe bounded-input diagnostic."""

    def __init__(self, diagnostic: InputDiagnostic) -> None:
        super().__init__(diagnostic.summary)
        self.diagnostic = diagnostic


class _DuplicateObjectField(ValueError):
    pass


def _fail(
    summary: str,
    detail: str,
    *,
    source_line: int | None = None,
) -> NoReturn:
    raise InputFailure(InputDiagnostic(summary, detail, source_line))


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


def _validate_shape(value: object, *, source_line: int) -> JSONValue:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_VALUE_NODES:
            _fail(
                "JSON record is too complex",
                f"Source line {source_line} exceeds {MAX_VALUE_NODES} values.",
                source_line=source_line,
            )
        if depth > MAX_NESTING_DEPTH:
            _fail(
                "JSON record is nested too deeply",
                f"Source line {source_line} exceeds depth {MAX_NESTING_DEPTH}.",
                source_line=source_line,
            )
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                _fail(
                    "JSON object key is invalid",
                    f"Source line {source_line} contains a non-text object key.",
                    source_line=source_line,
                )
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is None or type(item) in {str, int, float, bool}:
            continue
        else:  # pragma: no cover - json.loads cannot construct other types.
            _fail(
                "JSON value is unsupported",
                f"Source line {source_line} contains an unsupported value.",
                source_line=source_line,
            )
    return cast(JSONValue, value)


def parse_jsonl(source: bytes) -> Snapshot:
    """Parse one immutable source snapshot or raise a content-safe failure."""

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if len(source) > MAX_SOURCE_BYTES:
        _fail(
            "JSONL snapshot is too large",
            f"Snapshot exceeds the {MAX_SOURCE_BYTES}-byte read bound.",
        )
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        source_line = source[: exc.start].count(b"\n") + 1
        _fail(
            "JSONL snapshot is not strict UTF-8",
            f"Invalid UTF-8 begins at byte {exc.start} on source line {source_line}.",
            source_line=source_line,
        )

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return Snapshot((), len(source))
    if len(lines) > MAX_RECORDS:
        _fail(
            "JSONL snapshot has too many records",
            f"Snapshot exceeds the {MAX_RECORDS}-record bound.",
        )

    records: list[Record] = []
    for source_line, raw_line in enumerate(lines, start=1):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        line_size = len(line.encode("utf-8"))
        if not line:
            _fail(
                "JSONL snapshot contains a blank record",
                f"Source line {source_line} is blank.",
                source_line=source_line,
            )
        if line_size > MAX_RECORD_BYTES:
            _fail(
                "JSONL record is too large",
                f"Source line {source_line} exceeds {MAX_RECORD_BYTES} UTF-8 bytes.",
                source_line=source_line,
            )
        try:
            value = json.loads(
                line,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
        except _DuplicateObjectField:
            _fail(
                "JSONL record contains a duplicate field",
                f"Source line {source_line} repeats an object field.",
                source_line=source_line,
            )
        except json.JSONDecodeError:
            _fail(
                "JSONL record is malformed",
                f"Source line {source_line} contains malformed JSON.",
                source_line=source_line,
            )
        except (RecursionError, ValueError):
            _fail(
                "JSONL record is malformed",
                f"Source line {source_line} contains an invalid JSON value.",
                source_line=source_line,
            )
        records.append(
            Record(
                source_line=source_line,
                utf8_bytes=line_size,
                value=_validate_shape(value, source_line=source_line),
            )
        )
    return Snapshot(tuple(records), len(source))
