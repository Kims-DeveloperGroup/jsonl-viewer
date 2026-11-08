"""Keep repository-owned terminal previews synchronized with the renderer."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer.inspection import (
    MISSING, Context, Facet, Group, Inspection, Option, SearchScope, Section, Target,
)

from tests.support import FakeHost


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "docs" / "samples"


def _spec() -> ViewerSpec:
    return ViewerSpec(
        "session-01",
        "conversation-01",
        "agent-01",
    )


def _ordinary_source() -> bytes:
    return (
        b'{"timestamp":"2026-08-29T09:00:00Z","request_type":"request",'
        b'"content":"Find the mismatch.","latency_ms":12}\n'
        b'{"timestamp":"2026-08-29T09:00:01Z","request_type":"response",'
        b'"content":"The field is missing.","latency_ms":23}\n'
    )


def _nested_json_source() -> bytes:
    response = {
        "status": "provider_error",
        "details": ["correlation token mismatch", {"retryable": False}],
    }
    content = {
        "action": "provider_response",
        "response_text": json.dumps(response, separators=(",", ":")),
    }
    return (
        json.dumps(
            {
                "timestamp": "2026-08-29T09:00:00Z",
                "request_type": "response",
                "content": json.dumps(content, separators=(",", ":")),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _truncated_nested_leaf_source() -> bytes:
    content = json.dumps(
        {"short": "visible", "long": "한" * 2_000},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        json.dumps(
            {
                "timestamp": "2026-08-29T09:00:00Z",
                "request_type": "response",
                "content": content,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _frame(
    source: bytes,
    events: tuple[str | None, ...],
    size: tuple[int, int],
) -> str:
    host = FakeHost(events, size=size, color=False)
    view_jsonl(source, _spec(), host)
    return host.frames[-1]


def sample_frames() -> dict[str, str]:
    """Render the exact examples maintained by the repository owner."""
    long_source = (
        json.dumps(
            {
                "timestamp": "2026-08-29",
                "request_type": "response",
                "content": "한" * 2_000,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    cases = {
        "horizontal-scroll.txt": (
            json.dumps({"content": "abcdefghijklmnopqrstuvwxyz0123456789" * 4}).encode() + b"\n",
            ("scroll_right", "close"), (72, 10)),
        "cursor-paging.txt": (
            json.dumps({"content": list(range(12))}).encode() + b"\n",
            ("page_down", "page_up", "close"), (88, 10)),
        "simple.txt": (_ordinary_source(), ("close",), (88, 20)),
        "verbose.txt": (
            _ordinary_source(),
            ("toggle_mode", "close"),
            (88, 20),
        ),
        "search-matches.txt": (
            _ordinary_source(),
            ("search\tr", "close"),
            (88, 20),
        ),
        "nested-expanded-json.txt": (
            _nested_json_source(),
            ("close",),
            (100, 24),
        ),
        "cursor-folding.txt": (
            _nested_json_source(),
            ("cursor_down", "cursor_down", "cursor_down", "toggle_fold", "close"),
            (100, 24),
        ),
        "record-navigation.txt": (_ordinary_source(), ("page_up", "page_down", "close"), (88, 20)),
        "sibling-navigation.txt": (
            _nested_json_source(),
            ("next_sibling", "close"),
            (100, 24),
        ),
        "cursor-idle.txt": (
            _nested_json_source(),
            ("idle", "close"),
            (100, 24),
        ),
        "folded-search.txt": (
            _nested_json_source(),
            ("search\tcorrelation token mismatch", "toggle_fold", "close"),
            (100, 24),
        ),
        "truncated-content.txt": (long_source, ("close",), (72, 10)),
        "truncated-nested-leaves.txt": (
            _truncated_nested_leaf_source(),
            ("close",),
            (180, 12),
        ),
        "malformed-input.txt": (
            b'{"timestamp":"2026",}\n',
            ("close",),
            (72, 9),
        ),
        "tiny-terminal.txt": (_ordinary_source(), ("close",), (28, 6)),
        "plain-no-color.txt": (_ordinary_source(), ("close",), (64, 10)),
    }
    frames = {filename: _frame(source, events, size) + "\n"
              for filename, (source, events, size) in cases.items()}
    frames.update(_generic_frames())
    return frames


def _generic_frames() -> dict[str, str]:
    """Reproduce independent audit previews through the public host loop."""
    records = (
        {"trace": "a", "kind": "open", "level": "info", "message": "ready"},
        {"trace": "a", "kind": "read", "level": "error", "message": "timeout"},
        {"message": "independent", "schema": "customer_account"},
        {"trace": "a", "kind": "read", "level": "warn", "message": "retry"},
        {"trace": "a", "kind": "write", "level": "info", "message": "saved"},
        {"trace": "a", "kind": "close", "level": "info", "message": "done"},
    )
    source = b"".join(json.dumps(record, separators=(",", ":")).encode() + b"\n" for record in records)
    spec = ViewerSpec(context=(Context("Dataset", "Audit events"),), input_protocol="keys",
                      inspection=Inspection(
                          groups=(Group("trace-a", "Trace A", (1, 2, 4, 5, 6), sections=(
                              Section("open", "Open", (1,)), Section("reads", "Reads", (2, 4)),
                              Section("writes", "Writes", (5,)), Section("close", "Close", (6,))),
                              values=(("level", "error"),)),),
                          facets=(Facet("level", "Level", (Option("info", "Info"), Option("error", "Error"),
                                                            Option(MISSING, "Missing")), path=("level",)),),
                          scopes=(SearchScope("messages", "Messages", tuple(Target(i, ("message",))
                                                                            for i in range(1, 7))),)))
    cases = {
        "generic-inspection.txt": ((None,), (140, 28)),
        "generic-group-list.txt": (("text\tv", "key\ttab", None), (140, 28)),
        "generic-name-find.txt": (("text\tv", "key\ttab", "text\tt", "text\tTrace", None), (140, 28)),
        "generic-filter-draft.txt": (("text\tf", "key\tenter", "key\tdown", "key\tdown", "key\tspace",
                                       "key\tenter", None), (80, 24)),
        "generic-scoped-search.txt": (("search\ttimeout", "text\t/", "key\ttab", "key\tright", "key\ttab",
                                        "key\tenter", None), (140, 28)),
    }
    frames = {}
    for name, (events, size) in cases.items():
        host = FakeHost(events, size=size, color=False)
        view_jsonl(source, spec, host)
        if host.close_calls != 1:
            raise AssertionError("Sample host ownership was not restored")
        frames[name] = host.frames[-1] + "\n"
    return frames


class DocumentedSampleTests(unittest.TestCase):
    def test_every_documented_plain_view_is_exact(self) -> None:
        frames = sample_frames()
        self.assertEqual({path.name for path in SAMPLES.glob("*.txt")}, set(frames))
        for filename, frame in frames.items():
            with self.subTest(sample=filename):
                expected = (SAMPLES / filename).read_text(encoding="utf-8")
                self.assertEqual(expected, frame)


if __name__ == "__main__":
    unittest.main()
