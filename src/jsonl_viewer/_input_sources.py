"""Own optional keyboard-source changes for one interactive viewer call."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TextIO


@dataclass(frozen=True, slots=True)
class InputSourceState:
    """An adapter-owned identity and its verified English capability."""

    identity: tuple[str, ...]
    english: bool


class InputSourceAdapter(Protocol):
    def current_source(self) -> InputSourceState | None: ...
    def english_source(self) -> InputSourceState | None: ...
    def select_source(self, source: InputSourceState) -> None: ...


class InputSourceLifecycle:
    """Select English at command boundaries and retain exact entry ownership.

    A failed optional discovery or selection never changes viewer text or
    replaces a terminal failure. Source observation happens only at acquisition,
    mode changes, and cleanup; there is no per-key or idle polling.
    """

    def __init__(self, adapter_factory: Callable[
        [TextIO, TextIO], InputSourceAdapter | None,
    ] | None = None) -> None:
        self._factory = adapter_factory
        self._active = False
        self._adapter: InputSourceAdapter | None = None
        self._entry: InputSourceState | None = None
        self._english: InputSourceState | None = None
        self._mode: str | None = None
        self._restoration_pending = False

    def start(self, input_stream: TextIO, output_stream: TextIO, *, live: bool) -> None:
        if self._active:
            raise RuntimeError("input-source lifecycle is already active")
        self._active = True
        if not live or self._factory is None:
            return
        try:
            adapter = self._factory(input_stream, output_stream)
            if adapter is None:
                return
            entry = adapter.current_source()
            if not isinstance(entry, InputSourceState):
                return
            english = adapter.english_source()
            if not isinstance(english, InputSourceState) or not english.english:
                return
            self._adapter, self._entry, self._english = adapter, entry, english
        except Exception:
            return

    def set_input_mode(self, mode: str) -> None:
        if mode not in {"command", "text"}:
            raise ValueError("input mode must be 'command' or 'text'")
        if not self._active or mode == self._mode:
            return
        self._mode = mode
        if self._adapter is None:
            return
        # Retain cleanup responsibility even after a successful search restore:
        # the user may select another source while the search draft is open.
        self._restoration_pending = True
        if mode == "command":
            self._change(self._english, retain_english=True)
        else:
            self._change(self._entry)

    def stop(self) -> None:
        try:
            if self._restoration_pending:
                self._change(self._entry)
        finally:
            self._active = False
            self._adapter = None
            self._entry = None
            self._english = None
            self._mode = None
            self._restoration_pending = False

    def _change(self, target: InputSourceState | None, *, retain_english: bool = False) -> None:
        adapter = self._adapter
        if adapter is None or target is None:
            return
        try:
            current = adapter.current_source()
        except Exception:
            current = None
        if isinstance(current, InputSourceState):
            if current == target or (retain_english and current.english):
                return
        elif retain_english:
            return
        try:
            # Restoration must still be attempted after an uncertain partial
            # selection, even when observing the current source has failed.
            adapter.select_source(target)
            # A fresh read also refreshes native source caches after selection.
            adapter.current_source()
        except Exception:
            return
