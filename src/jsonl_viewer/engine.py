"""Coordinate one source-faithful transient view through an injected host."""
from __future__ import annotations
from dataclasses import replace
from .contracts import ViewerHost, ViewerSpec
from ._input import InputFailure, parse_jsonl
from ._model import Snapshot, ViewState
from ._render import RenderSession, render_frame, render_loading
from ._controls import _accept_frame, _transition

def view_jsonl(source: bytes, spec: ViewerSpec, host: ViewerHost) -> None:
    """View one immutable JSONL byte snapshot through an injected host.

    All navigation, mode, search, help, and message state is local to this call.
    The source is never written and no state is returned or persisted.
    """

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if not isinstance(spec, ViewerSpec):
        raise TypeError("spec must be a ViewerSpec")

    closed = False
    active_failure: BaseException | None = None
    try:
        mode_notifier = getattr(host, "set_input_mode", None)
        input_mode = None
        columns, rows = host.terminal_size()
        color = bool(host.color_enabled())
        host.present(
            render_loading(
                spec,
                columns=columns,
                rows=rows,
                color=color,
            )
        )
        diagnostic = None
        try:
            snapshot = parse_jsonl(source)
        except InputFailure as exc:
            snapshot = Snapshot((), len(source))
            diagnostic = exc.diagnostic

        if spec.inspection_enabled:
            from ._browser import view_browser
            view_browser(snapshot, spec, host, diagnostic)
            return
        state = ViewState()
        session = RenderSession()
        rendered = None
        geometry = None
        cursor_visible = True
        present_needed = True
        while not closed:
            desired_mode = ("text" if state.prompt is not None
                            and state.prompt.kind == "search_query" else "command")
            if desired_mode != input_mode:
                if callable(mode_notifier):
                    mode_notifier(desired_mode)
                input_mode = desired_mode
            current_geometry = (*host.terminal_size(), bool(host.color_enabled()))
            if rendered is None or current_geometry != geometry:
                columns, rows, color = current_geometry
                rendered = render_frame(spec, state, snapshot=snapshot, diagnostic=diagnostic,
                                        columns=columns, rows=rows, color=color, session=session)
                geometry = current_geometry
                present_needed = True
                state = _accept_frame(state, rendered)
            if present_needed:
                host.present(rendered.text if cursor_visible else (rendered.idle_text or rendered.text))
            event = host.read_event()
            if event == "idle":
                present_needed = False
                if state.prompt is None and not state.help_visible and rendered.characters:
                    cursor_visible = not cursor_visible
                    present_needed = True
                continue
            cursor_visible = True
            present_needed = True
            previous = state
            state, closed = _transition(
                state, event, snapshot, spec, page_size=rendered.body_rows,
                selected_line_count=rendered.selected_line_count,
                malformed=diagnostic is not None, rendered=rendered, session=session,
            )
            if state.focus_property == previous.focus_property and (
                state.mode != previous.mode or state.search != previous.search
                or state.reveal_match):
                state = replace(state, focus_property=None)
            rendered = None
    except BaseException as failure:
        active_failure = failure
        raise
    finally:
        try:
            host.close_view()
        except BaseException:
            if active_failure is None:
                raise
