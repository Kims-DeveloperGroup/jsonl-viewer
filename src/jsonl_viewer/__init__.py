"""Public facade for the isolated read-only JSONL viewer."""

from __future__ import annotations

from .contracts import ViewerHost, ViewerSpec, ViewerTerminal
from .engine import view_jsonl as _view_with_host

__all__ = ["ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl"]


def view_jsonl(
    source: bytes,
    spec: ViewerSpec,
    host: ViewerHost | None = None,
    *,
    terminal: ViewerTerminal | None = None,
    transient_only: bool = False,
) -> None:
    """View immutable bytes with a borrowed host or a viewer-owned terminal."""

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if not isinstance(spec, ViewerSpec):
        raise TypeError("spec must be ViewerSpec")
    if type(transient_only) is not bool:
        raise TypeError("transient_only must be bool")
    if terminal is not None and not isinstance(terminal, ViewerTerminal):
        raise TypeError("terminal must be ViewerTerminal or None")
    if host is not None:
        if terminal is not None:
            raise ValueError("host and terminal are mutually exclusive")
        if transient_only:
            raise ValueError("transient_only requires viewer-owned terminal")
        _view_with_host(source, spec, host)
        return
    # Importing the facade and borrowing a host never acquire terminal resources.
    from ._terminal import view_owned

    view_owned(source, spec, terminal, transient_only=transient_only)
