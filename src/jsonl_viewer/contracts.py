"""Immutable public contracts for embedding the JSONL viewer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, TextIO

from .inspection import Context, Inspection


_MAX_HEADER_VALUE_CHARACTERS = 512
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
    """Describe an immutable view with optional generic inspection metadata.

    With no historical identity headers, every JSON field remains visible in
    Simple mode. Context and inspection descriptors are inert values. Legacy
    three-header calls retain their projection and terminal behavior.
    """

    session_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    date_time_field: str = "timestamp"
    request_type_field: str = "request_type"
    content_field: str = "content"
    title: str = "JSONL Viewer"
    conversation_label: str = "Conversation"
    conversation_subject: str | None = None
    input_protocol: str = "semantic"
    context: tuple[Context, ...] = ()
    inspection: Inspection | None = None

    def __post_init__(self) -> None:
        if type(self.input_protocol) is not str or self.input_protocol not in {
            "semantic", "keys"
        }:
            raise ValueError("input_protocol must be 'semantic' or 'keys'")
        legacy = (self.session_id, self.conversation_id, self.agent_id)
        if any(value is not None for value in legacy):
            if any(value is None for value in legacy):
                raise ValueError("legacy headers require all three values")
            for value in legacy:
                _validate_header_value(value, label="legacy header")
        if type(self.context) is not tuple or len(self.context) > 64 or any(
            type(item) is not Context for item in self.context
        ):
            raise TypeError("context must be a bounded tuple of Context values")
        if self.inspection is not None and type(self.inspection) is not Inspection:
            raise TypeError("inspection must be an Inspection or None")
        for attribute, label in (
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
    def primary_fields(self) -> tuple[str, ...]:
        """Return the exact Simple-mode field priority."""

        return (
            self.date_time_field,
            self.request_type_field,
            self.content_field,
        ) if self.session_id is not None and self.inspection is None else ()

    @property
    def display_context(self) -> tuple[Context, ...]:
        """Convert historical header inputs at the compatibility boundary."""
        if self.session_id is None:
            return self.context
        subject = self.conversation_id
        if self.conversation_subject is not None:
            subject = f"{subject} — {self.conversation_subject}"
        return (
            Context("Session", self.session_id, "S"),
            Context(self.conversation_label, subject),
            Context("Agent", self.agent_id, "A"),
        ) + self.context

    @property
    def inspection_enabled(self) -> bool:
        """Keep selection of the compatibility path outside reusable engines."""
        return self.inspection is not None or self.session_id is None


@dataclass(frozen=True, slots=True)
class ViewerTerminal:
    """Supply transport while transferring one view's terminal ownership.

    Streams remain caller-owned and are never closed. ``read_character`` may
    supply an existing buffered UTF-8 reader; it receives a readiness callback
    positionally and invokes it only before reading new bytes, not before
    delivering already buffered characters. It returns one decoded character
    or reports EOF with an empty string or ``EOFError``.
    """

    input_stream: TextIO
    output_stream: TextIO
    read_character: Callable[[Callable[[], None] | None], str] | None = None
    no_color: bool = False

    def __post_init__(self) -> None:
        if type(self.no_color) is not bool:
            raise TypeError("no_color must be bool")
        for stream, methods, label in (
            (self.input_stream, ("read", "readline", "isatty"), "input_stream"),
            (self.output_stream, ("write", "flush", "isatty"), "output_stream"),
        ):
            if any(not callable(getattr(stream, method, None)) for method in methods):
                raise TypeError(f"{label} must be a text stream")
        if self.read_character is not None and not callable(self.read_character):
            raise TypeError("read_character must be callable or None")


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
    ``close``, ``goto<TAB>LINE``, or ``search<TAB>QUERY``.

    ``input_protocol='keys'`` additionally accepts ``text<TAB>TEXT``,
    ``line<TAB>COMMAND``, ``key<TAB>NAME``, and
    ``mouse<TAB>BUTTON<TAB>COLUMN<TAB>ROW<TAB>PHASE``. Mouse buttons are decimal
    0..255; coordinates are positive one-based terminal cells of at most six
    digits; phase is ``press`` or ``release``. The viewer consumes mouse input
    during prompts/help and ignores malformed or unsupported reports. Wheel
    presses reuse j/k cursor movement; left presses focus visible JSON cells.
    Each text/key/line payload is bounded to
    8,192 characters, independently of its envelope. Text contains printable
    Unicode code points; line input may also contain tab separators. Hosts
    preserve literal line input: the viewer owns command grammar and bindings.
    Key names are ``enter``, ``escape``, ``unknown_escape``, ``backspace``,
    ``delete``, ``left``, ``right``, ``home``, ``end``, ``up``, ``down``,
    ``page_up``, ``page_down``, ``ctrl_a``, ``ctrl_e``, ``ctrl_u``, ``ctrl_k``,
    ``ctrl_w``, ``tab``, ``shift_tab``, ``space``, ``interrupt``, ``eof``, and ``unknown``. Hosts decode physical
    controls and bounded escape sequences without applying viewer actions.
    EOF closes the view. Interrupt closes an active prompt's view and otherwise
    applies semantic cancel. Bare or unsupported Escape cancels only
    an active prompt draft; main-view Escape retains semantic cancel behavior.
    Main-view character cursors and container folds are transient. Hosts may send
    ``cursor_left``, ``cursor_right``, ``cursor_up``, ``cursor_down``, and
    ``toggle_fold``, ``next_sibling``, and ``previous_sibling`` semantic events.
    ``scroll_left`` and ``scroll_right`` pan the JSON body by half its width;
    physical Left/Right and ordinary-line ``left``/``right`` select those actions
    outside prompts. Headers, record/line gutters, and footers remain fixed.
    Interactive hosts may return ``idle`` after 500 ms without input; the engine
    alternates cached cursor emphasis while leaving prompt/help frames steady.
    Hosts that never return ``idle`` retain a steady cursor. Partial UTF-8 input
    must remain buffered across idle timeouts. Plain frames reserve a caret row; ANSI frames
    use a readable inverse-video character cell.
    Prompts and their logical cursors are rendered in ordinary complete frames;
    hosts never acquire a second input lifecycle or edit a prompt buffer.

    A host may additionally expose ``set_input_mode(mode)`` to observe input
    context. This optional capability is resolved once per call and notified
    before the first input and only when the context changes. ``mode`` is
    ``"text"`` for a search-query draft and ``"command"`` for navigation,
    help, goto, and other viewer states. Search validation errors retain text
    mode. The host owns any resulting keyboard-source changes and restoration.
    Existing five-method hosts require no additional method or resource work.
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
