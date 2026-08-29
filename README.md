# jsonl-viewer

`jsonl-viewer` is a read-only, transient, structured JSONL viewer for Python
terminals. It parses one bounded immutable byte snapshot, renders colored
multiline JSON with source-record line gutters, and discards navigation,
search, help, and mode state when the view closes.

The package is Python 3.11+, MIT licensed, and has no runtime dependencies. It
has no knowledge of Story, agent runtimes, storage layouts, provider schemas,
or terminal-driver implementations.

## What 0.1.0 provides

- a three-name public API: `ViewerSpec`, `ViewerHost`, and `view_jsonl`;
- Simple and Verbose views, with configurable date/time, request-type, and
  content fields ordered first;
- a 4 KiB UTF-8 Simple content preview and explicit truncation metadata;
- strict UTF-8 JSONL parsing with bounded, content-safe malformed-input views;
- stable source-record line gutters, record navigation, paging, and exact
  go-to-line;
- case-insensitive substring search over only the fields enumerated by the
  embedding application, with `n`/`N`, current/total results, and automatic
  Verbose promotion for a hidden-field hit;
- deterministic semantic ANSI or exactly equivalent plain output;
- Unicode-aware cell clipping and visible neutralization of embedded terminal
  controls, bidi controls, and other format controls; and
- a standalone terminal owner plus an injected host boundary for applications
  that already own raw mode, signals, geometry, input, output, and cleanup.

There is no writer API, persistence, live tail, network access, replay, retry,
or arbitrary search-expression language.

## Preview

This excerpt shows the plain semantic view. ANSI-capable terminals add color
without changing the text, markers, or layout:

```text
JSONL VIEWER • READ ONLY • SIMPLE
Session: session-01 • Debate: d20260829T090000_abcd1234 — Provider diagnostics • Agent: agent-01
@ 1 │ {
@ 1 │   "timestamp": "2026-08-29T09:00:00Z",
@ 1 │   "request_type": "request",
@ 1 │   "content": "Find the mismatch."
@ 1 │ }

* 2 │ {
* 2 │   "timestamp": "2026-08-29T09:00:01Z",
* 2 │   "request_type": "response",
* 2 │   "content": "The field is missing."
* 2 │ }
Search request_type='r' • 1/2 • @ current, * other
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
```

The [design system](docs/design-system.md) defines every visual role and user
state. Seven [deterministic full-frame samples](docs/samples/README.md) cover
Simple, Verbose, search, truncation, malformed input, tiny terminals, and
plain / `NO_COLOR` output.

## Install and run

```bash
python -m pip install jsonl-viewer
jsonl-viewer exchanges.jsonl \
  --session session-01 \
  --conversation debate-full-id \
  --conversation-label Debate \
  --conversation-subject "Provider response diagnostics" \
  --agent agent-01 \
  --searchable-field timestamp \
  --searchable-field request_type \
  --searchable-field content
```

`NO_COLOR` or `--no-color` disables ANSI color without changing any semantic
text or marker. A non-TTY uses an ordinary-line command fallback. Use `-` as
the path to snapshot standard input; because that consumes stdin, the view is
then rendered once and closes on EOF.

Standalone keys are `↑`/`↓` or `j`/`k`, Page Up/Page Down or `b`/Space, `g`,
`/`, `n`, `N`, `m`, `h`/`?`, Escape, and `q`. In ordinary-line mode, use
`g LINE` and `/ FIELD QUERY`.

## Embed without transferring terminal ownership

```python
from jsonl_viewer import ViewerHost, ViewerSpec, view_jsonl

spec = ViewerSpec(
    session_id="session-01",
    conversation_id="debate-full-id",
    agent_id="agent-01",
    searchable_fields=("timestamp", "request_type", "content"),
    conversation_label="Debate",
    conversation_subject="Provider response diagnostics",
)

# `host` implements ViewerHost using the application's existing terminal
# driver. The viewer never enters raw mode, clears a screen, handles signals,
# reads a file, or closes that terminal.
view_jsonl(snapshot_bytes, spec, host)
```

`ViewerHost` supplies `terminal_size()`, `color_enabled()`, `present(frame)`,
`read_event()`, and `close_view()`. Frames contain printable text, newlines,
and optionally ANSI SGR color sequences—never cursor movement, alternate-screen,
or terminal-mode controls.

Header strings are generic display values, bounded to 512 characters and
neutralized before rendering. `conversation_id` remains the exact scope ID;
`conversation_label` names its generic kind and defaults to `Conversation`;
`conversation_subject` is an optional separate subject. The renderer combines
them as `Conversation: FULL_ID — subject` or `Debate: FULL_ID — subject`, so an
embedding application never has to concatenate display text into the ID. The
embedding application owns the scope vocabulary and any picker used to select
it. Responsive clipping can shorten the combined header line but never mutates
those `ViewerSpec` values.

The closed event vocabulary is:

```text
up | down | page_up | page_down | next_match | previous_match
toggle_mode | help | cancel | clear_search | close
goto<TAB>POSITIVE_SOURCE_LINE
search<TAB>ENUMERATED_TOP_LEVEL_FIELD<TAB>NONEMPTY_QUERY
```

`None` means EOF/close. Unknown events produce a transient help hint. The host
decides how keys, commands, resize events, focus, and terminal restoration map
onto this protocol.

## Input and safety contract

`view_jsonl` accepts exact immutable `bytes`. It never accepts a writable
stream or path and never mutates the object. The standalone adapter opens a
path only for a bounded binary read.

- snapshot bound: 16,777,216 bytes;
- record bound: 2,097,152 UTF-8 bytes;
- record-count bound: 10,000;
- nesting bound: 64 levels;
- value-node bound: 200,000 per record;
- Simple string/content preview: at most 4,096 UTF-8 bytes at a complete code
  point; and
- Verbose string preview: at most 65,536 UTF-8 bytes.

Blank records, duplicate object fields, non-finite numbers, malformed JSON,
invalid UTF-8, and exceeded bounds render content-safe diagnostics. Source
values are untrusted: control and format characters are rendered as visible
`\uXXXX` text. Color is never the only signal.

Simple mode is an orientation view, not a privacy boundary. Verbose mode can
show every field in a valid record, and an enumerated field is a search limit,
not a visibility or authorization rule. The embedding application is
responsible for authorizing, selecting, retaining, and redacting snapshot
data. The library performs no filesystem or network I/O, telemetry, or
persistence; the standalone adapter performs only its explicit bounded file or
standard-input read.

## Design and verification

- [Terminal design system, previews, and hosted scenarios](docs/design-system.md)
- [Source-controlled deterministic sample views](docs/samples/README.md)
- [Python module inventory](PYTHON_MODULE_INDEX.md)
- [Python modularity policy](.codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md)

Run the dependency-free checks from a checkout:

```bash
PYTHONPATH=src python -W error -m unittest discover -s tests -v
python -m compileall -q src tests
```
