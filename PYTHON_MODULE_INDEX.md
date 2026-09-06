# Python Module Index

## Scope and authority

This index describes the current importable production units under
`src/jsonl_viewer/`. It is a navigation record, not a plan or substitute for
the [Python Modularity Policy](.codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md).

Supported public imports are derived from `jsonl_viewer.__all__`, documented
root imports, and the executable entry point in `pyproject.toml`. Direct module
imports used only by tests remain internal.

Source root: `src/`

Package root: `src/jsonl_viewer/`

Importable production units indexed: 8.

Direct internal dependency edges indexed: 13.

Directed internal dependency cycles indexed: 0.

`py.typed` is intentional package data, not an importable unit.

## Current dependency direction

```text
jsonl_viewer -> contracts
             -> engine -> contracts
                       -> _input -> _model
                       -> _model
                       -> _render -> contracts/_model

__main__ -> _standalone -> contracts/engine/_input
```

The facade and standalone entry points depend inward on the stable generic
contracts and transient engine. The engine depends on bounded parsing,
immutable private state, and deterministic rendering. Reusable internals never
depend on the standalone terminal owner. The graph is acyclic and no production
unit imports Story or any third-party runtime package.

## Package and module entries

### `jsonl_viewer`

- **Source:** `src/jsonl_viewer/__init__.py`
- **Responsibility:** Provide the intentional stable library facade.
- **Supported surface:** Exactly `ViewerSpec`, `ViewerHost`, and `view_jsonl` in
  `__all__` and as root imports. No internal terminal type is exported.
- **Direct internal dependencies:** `contracts` and `engine`.
- **State, resources, and side effects:** Owns no state or external resource;
  import loads the public contracts and engine without I/O or terminal work.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `tests/test_standalone_and_packaging.py`, and `README.md`.

### `jsonl_viewer.__main__`

- **Source:** `src/jsonl_viewer/__main__.py`
- **Responsibility:** Start the standalone viewer for `python -m jsonl_viewer`.
- **Supported surface:** Module execution only; no reusable API.
- **Direct internal dependencies:** `_standalone`.
- **State, resources, and side effects:** Module execution delegates to
  `_standalone.main()` and raises `SystemExit`; ordinary package import does not
  import this module.
- **Primary verification/documentation:**
  `tests/test_standalone_and_packaging.py` and the README standalone workflow.

### `jsonl_viewer._input`

- **Source:** `src/jsonl_viewer/_input.py`
- **Responsibility:** Parse one immutable strict UTF-8 JSONL snapshot under
  fixed content-safe bounds.
- **Supported surface:** No supported consumer API. Private `parse_jsonl`, its
  bound constants, and `InputFailure` are used by the engine and standalone
  read adapter.
- **Direct internal dependencies:** `_model`.
- **State, resources, and side effects:** Owns no mutable state or external
  resource. Parsing performs no I/O, validates source/record/count/depth/node
  bounds, rejects blank/duplicate/non-finite/malformed data, and returns frozen
  snapshot records or a content-safe diagnostic.
- **Primary verification/documentation:** `tests/test_input_and_render.py`,
  `tests/test_public_api_and_engine.py`, and the README input contract.

### `jsonl_viewer._model`

- **Source:** `src/jsonl_viewer/_model.py`
- **Responsibility:** Define immutable private snapshot and transient view-state
  values.
- **Supported surface:** No supported consumer API. The parser, engine, and
  renderer share frozen record, snapshot, diagnostic, mode, search, prompt,
  view, and render-result values. Prompt values hold an uncommitted stage,
  buffer, code-point cursor, selected search field, and local validation error.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Defines values only; it owns no module
  state, lifecycle, or I/O.
- **Primary verification/documentation:** exercised through all engine, input,
  rendering, sample, and standalone tests; state behavior is documented in
  `docs/design-system.md`.

### `jsonl_viewer._render`

- **Source:** `src/jsonl_viewer/_render.py`
- **Responsibility:** Render deterministic bounded semantic ANSI/plain frames
  from call-local derived JSON projections.
- **Supported surface:** No supported consumer API. The engine consumes private
  loading/frame renderers and the current record-line count; tests use the
  private SGR-stripping verifier.
- **Direct internal dependencies:** `_model` and `contracts`.
- **State, resources, and side effects:** Owns immutable role/bound metadata,
  one call-local projection budget, and pure formatting behavior only. It
  performs no terminal or filesystem I/O. Rendering reorders/project fields by
  `ViewerSpec`, strictly and recursively expands only complete string-encoded
  JSON objects or arrays without mutating source/search values, and rejects
  duplicate, non-finite, malformed, scalar, fragment, and over-bound display
  interpretations. Transactional layer/depth/node/decoded-byte bounds retain
  an over-bound candidate as its original string with a content-safe cue.
  String-leaf preview bounds apply only after expansion at complete UTF-8 code
  points; visible per-leaf markers and footer aggregates report expansion,
  skipped, and retained/full truncation facts without removing keys,
  containers, delimiters, or child presence. The same renderer formats
  multiline JSON, projects the supplied scope label, exact ID, and optional
  subject into the header, neutralizes controls, computes Unicode cell
  clipping, and emits optional SGR without cursor/lifecycle controls. Active
  search-field, search-query, and goto editors occupy the two footer rows;
  bounded horizontal windows keep a printable logical cursor visible and
  display prompt-local validation errors without altering committed state.
- **Primary verification/documentation:** `tests/test_input_and_render.py`,
  `tests/test_documented_samples.py`, and `docs/design-system.md`.

### `jsonl_viewer._standalone`

- **Source:** `src/jsonl_viewer/_standalone.py`
- **Responsibility:** Adapt the generic viewer to one standalone CLI-owned
  terminal lifecycle.
- **Supported surface:** The installed `jsonl-viewer` entry point targets
  internal `main(argv=None)`. `_TerminalHost` and read/parser helpers are not
  supported library APIs. The CLI accepts the exact conversation/scope ID,
  scope label (default `Conversation`), and optional scope subject separately.
- **Direct internal dependencies:** `_input`, `contracts`, and `engine`.
- **State, resources, and side effects:** A `main` call opens its selected source
  only for a bounded binary read. On a supported interactive POSIX terminal its
  private context owns cbreak mode, alternate screen, cursor visibility, key
  decoding, repaint, and exact restoration. It selects the public `keys`
  input protocol and emits normalized physical keys, incrementally decoded
  text, or bounded literal line transport. Escape recognition is limited to
  32 bytes with a 20 ms deadline and 4,096-byte drain ceiling; overlong lines
  drain through their terminator up to 65,536 characters. A drain-bound failure
  raises fixed text instead of interpreting the remaining suffix. Configured
  terminal interrupt/EOF bytes are decoded before ordinary text. The host owns
  no viewer bindings, command grammar, prompt draft, search/goto action, or
  cancellation-specific retained frame. EOF and interrupt are delivered to
  the engine; terminal cleanup remains visible and host-owned. `NO_COLOR` and
  `--no-color` disable SGR. Import alone performs no filesystem or terminal
  operation; optional `termios`/`tty` imports select a platform fallback.
- **Primary verification/documentation:**
  `tests/test_standalone_and_packaging.py`, `README.md`, and the standalone
  scenario in `docs/design-system.md`.

### `jsonl_viewer.contracts`

- **Source:** `src/jsonl_viewer/contracts.py`
- **Responsibility:** Define the immutable generic public embedding contracts.
- **Supported surface:** Package-supported frozen `ViewerSpec` and structural
  `ViewerHost` protocol through the root facade. `ViewerSpec` supplies exact
  header values—including a separate scope label, exact scope ID, and optional
  subject—a closed ordered searchable-field enumeration, and configurable
  Simple-mode field identities. Its appended `input_protocol` field defaults
  to `semantic` for existing hosts; `keys` additionally enables bounded
  text/key/literal-line envelopes without changing the three-name facade or
  host method signatures. `ViewerHost` owns size/color decisions,
  complete-frame presentation, closed event delivery, and close restoration.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Defines and validates bounded immutable
  values/protocols only. Import and construction perform no I/O.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `README.md`, and `docs/design-system.md`.

### `jsonl_viewer.engine`

- **Source:** `src/jsonl_viewer/engine.py`
- **Responsibility:** Coordinate one transient read-only view through an
  injected host.
- **Supported surface:** Package-supported `view_jsonl(source, spec, host)`
  through the root facade. Its exact source is immutable `bytes`; it returns
  `None` and persists no view state.
- **Direct internal dependencies:** `_input`, `_model`, `_render`, and
  `contracts`.
- **State, resources, and side effects:** Owns navigation, within-record paging,
  Simple/Verbose, closed-field search, current result, help, and transient
  message state only for one call. In `keys` mode it additionally owns all
  navigation bindings, ordinary-line command grammar, and search/goto prompt
  stages, text editing, validation, and cancellation. Event payloads are bounded
  independently of envelope prefixes at 8,192 characters; field/goto drafts
  are limited to 128 and query drafts to 1,024 code points. Raw controls cannot
  enter a draft. Logical cursor edits include arrows, Home/End, Backspace,
  Delete, Ctrl+A/E, prefix deletion with Ctrl+U, suffix deletion with Ctrl+K,
  and previous-word deletion with Ctrl+W. Prompt Escape discards only the draft
  and preserves committed view state, including prior messages and search
  position. EOF closes; interrupt closes an active prompt's view and otherwise
  follows main-view cancel precedence. Help and malformed/empty snapshots
  cannot begin prompts. Legacy semantic events remain accepted in both input
  modes. It presents Loading before parsing, renders bounded snapshot/error
  states, and calls `host.close_view()` in `finally`. It performs no file,
  terminal-driver, network, persistence, replay, retry, or live-tail operation.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `tests/test_input_and_render.py`, `tests/test_documented_samples.py`,
  `README.md`, and `docs/design-system.md`.
