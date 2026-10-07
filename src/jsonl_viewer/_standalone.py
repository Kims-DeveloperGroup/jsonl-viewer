"""Standalone command/file adapter for the viewer-owned terminal."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

from . import view_jsonl
from ._input import MAX_SOURCE_BYTES
from .contracts import ViewerSpec, ViewerTerminal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsonl-viewer",
        description="View one immutable strict UTF-8 JSONL snapshot.",
    )
    parser.add_argument("path", help="JSONL path, or - to snapshot standard input")
    parser.add_argument("--session", required=True, help="header session value")
    parser.add_argument(
        "--conversation",
        required=True,
        help="header conversation/scope ID",
    )
    parser.add_argument(
        "--conversation-label",
        default="Conversation",
        help="header scope label, such as Conversation or Debate",
    )
    parser.add_argument(
        "--conversation-subject",
        help="optional header scope subject",
    )
    parser.add_argument("--agent", required=True, help="header agent value")
    parser.add_argument("--date-time-field", default="timestamp")
    parser.add_argument("--request-type-field", default="request_type")
    parser.add_argument("--content-field", default="content")
    parser.add_argument("--title", default="JSONL Viewer")
    parser.add_argument("--no-color", action="store_true")
    return parser


def _read_bounded(handle: BinaryIO) -> bytes:
    return handle.read(MAX_SOURCE_BYTES + 1)


def main(argv: list[str] | None = None) -> int:
    """Run the standalone terminal owner without changing the source file."""

    arguments = _parser().parse_args(argv)
    owned_input: TextIO | None = None
    try:
        if arguments.path == "-":
            source = _read_bounded(sys.stdin.buffer)
            owned_input = open(os.devnull, encoding="utf-8")
            input_stream: TextIO = owned_input
        else:
            path = Path(arguments.path)
            with path.open("rb") as handle:
                source = _read_bounded(handle)
            input_stream = sys.stdin
        spec = ViewerSpec(
            session_id=arguments.session,
            conversation_id=arguments.conversation,
            agent_id=arguments.agent,
            date_time_field=arguments.date_time_field,
            request_type_field=arguments.request_type_field,
            content_field=arguments.content_field,
            title=arguments.title,
            conversation_label=arguments.conversation_label,
            conversation_subject=arguments.conversation_subject,
            input_protocol="keys",
        )
    except (OSError, TypeError, ValueError) as exc:
        if owned_input is not None:
            owned_input.close()
        print(f"jsonl-viewer: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        view_jsonl(
            source, spec,
            terminal=ViewerTerminal(input_stream, sys.stdout, no_color=arguments.no_color),
        )
    finally:
        if owned_input is not None:
            owned_input.close()
    return 0
