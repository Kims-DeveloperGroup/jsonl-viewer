"""Keep repository-owned terminal previews synchronized with the renderer."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonl_viewer import ViewerSpec, view_jsonl

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


class DocumentedSampleTests(unittest.TestCase):
    def test_every_documented_plain_view_is_exact(self) -> None:
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
        self.assertEqual(
            {path.name for path in SAMPLES.glob("*.txt")},
            set(cases),
        )
        for filename, (source, events, size) in cases.items():
            with self.subTest(sample=filename):
                expected = (SAMPLES / filename).read_text(encoding="utf-8")
                self.assertEqual(expected, _frame(source, events, size) + "\n")


if __name__ == "__main__":
    unittest.main()
