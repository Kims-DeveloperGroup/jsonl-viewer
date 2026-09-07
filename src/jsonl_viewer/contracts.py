"""Immutable public contracts for embedding the JSONL viewer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


_MAX_HEADER_VALUE_CHARACTERS = 512
_MAX_SEARCHABLE_FIELDS = 64
_MAX_FIELD_CHARACTERS = 128


def _validate_header_value(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be non-empty text")
    if len(value) > _MAX_HEADER_VALUE_CHARACTERS:
        raise ValueError(f"{label} is too long")
    return value


def _validate_field(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be non-empty text")
    if len(value) > _MAX_FIELD_CHARACTERS:
        raise ValueError(f"{label} is too long")
    if any(character in value for character in ("\x00", "\n", "\r", "\t")):
        raise ValueError(f"{label} contains a control separator")
    return value


@dataclass(frozen=True, slots=True)
class ViewerSpec:
    """Describe one generic immutable view with unrestricted literal search.

    ``searchable_fields`` retains bounded compatibility presets; an empty
    tuple is valid and presets never restrict search. Primary display fields
    identify exact top-level keys supplied by the embedding application.
    """

    session_id: str
    conversation_id: str
    agent_id: str
    searchable_fields: tuple[str, ...]
    date_time_field: str = "timestamp"
    request_type_field: str = "request_type"
    content_field: str = "content"
    title: str = "JSONL Viewer"
    conversation_label: str = "Conversation"
    conversation_subject: str | None = None
    input_protocol: str = "semantic"

    def __post_init__(self) -> None:
        if type(self.input_protocol) is not str or self.input_protocol not in {
            "semantic", "keys"
        }:
            raise ValueError("input_protocol must be 'semantic' or 'keys'")
        for attribute, label in (
            ("session_id", "session_id"),
            ("conversation_id", "conversation_id"),
            ("agent_id", "agent_id"),
            ("title", "title"),
            ("conversation_label", "conversation_label"),
        ):
            object.__setattr__(
                self,
                attribute,
                _validate_header_value(getattr(self, attribute), label=label),
            )

        if self.conversation_subject is not None:
            object.__setattr__(
                self,
                "conversation_subject",
                _validate_header_value(
                    self.conversation_subject,
                    label="conversation_subject",
                ),
            )

        try:
            fields = tuple(self.searchable_fields)
        except TypeError as exc:
            raise TypeError("searchable_fields must be an iterable of text") from exc
        if len(fields) > _MAX_SEARCHABLE_FIELDS:
            raise ValueError("searchable_fields contains too many fields")
        validated = tuple(
            _validate_field(field, label="searchable field") for field in fields
        )
        if len(set(validated)) != len(validated):
            raise ValueError("searchable_fields contains duplicates")
        object.__setattr__(self, "searchable_fields", validated)

        primary = tuple(
            _validate_field(getattr(self, attribute), label=attribute)
            for attribute in (
                "date_time_field",
                "request_type_field",
                "content_field",
            )
        )
        if len(set(primary)) != len(primary):
            raise ValueError("primary display fields must be distinct")
        object.__setattr__(self, "date_time_field", primary[0])
        object.__setattr__(self, "request_type_field", primary[1])
        object.__setattr__(self, "content_field", primary[2])

    @property
    def primary_fields(self) -> tuple[str, str, str]:
        """Return the exact Simple-mode field priority."""

        return (
            self.date_time_field,
            self.request_type_field,
            self.content_field,
        )


class ViewerHost(Protocol):
    """Injected terminal-lifecycle boundary used by :func:`view_jsonl`.

    The viewer emits printable full-frame text containing only newlines and,
    when enabled by ``color_enabled``, ANSI SGR color sequences. It never emits
    cursor, alternate-screen, or raw-mode controls. The host owns geometry,
    event decoding, presentation, signals, terminal modes, and restoration.

    With the default ``ViewerSpec.input_protocol='semantic'``, ``read_event``
    returns ``None`` for EOF or one closed event string:
    ``up``, ``down``, ``page_up``, ``page_down``, ``next_match``,
    ``previous_match``, ``toggle_mode``, ``help``, ``cancel``, ``clear_search``,
    ``close``, ``goto<TAB>LINE``, or ``search<TAB>FIELD<TAB>QUERY``.

    ``input_protocol='keys'`` additionally accepts ``text<TAB>TEXT``,
    ``line<TAB>COMMAND``, and ``key<TAB>NAME``. Each payload is bounded to
    8,192 characters, independently of its envelope. Text contains printable
    Unicode code points; line input may also contain tab separators. Hosts
    preserve literal line input: the viewer owns command grammar and bindings.
    Key names are ``enter``, ``escape``, ``unknown_escape``, ``backspace``,
    ``delete``, ``left``, ``right``, ``home``, ``end``, ``up``, ``down``,
    ``page_up``, ``page_down``, ``ctrl_a``, ``ctrl_e``, ``ctrl_u``, ``ctrl_k``,
    ``ctrl_w``, ``interrupt``, ``eof``, and ``unknown``. Hosts decode physical
    controls and bounded escape sequences without applying viewer actions.
    EOF closes the view. Interrupt closes an active prompt's view and otherwise
    applies semantic cancel. Bare or unsupported Escape cancels only
    an active prompt draft; main-view Escape retains semantic cancel behavior.
    Prompts and their logical cursors are rendered in ordinary complete frames;
    hosts never acquire a second input lifecycle or edit a prompt buffer.
    """

    def terminal_size(self) -> tuple[int, int]:
        """Return ``(columns, rows)`` for the next complete frame."""

        ...

    def color_enabled(self) -> bool:
        """Return whether semantic ANSI SGR colors may be included."""

        ...

    def present(self, frame: str) -> None:
        """Present one complete frame without transferring terminal ownership."""

        ...

    def read_event(self) -> str | None:
        """Return one closed viewer event, or ``None`` for EOF."""

        ...

    def close_view(self) -> None:
        """Restore the host's surrounding transient view after viewer close."""

        ...
