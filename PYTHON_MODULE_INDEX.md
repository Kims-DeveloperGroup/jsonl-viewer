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

Importable production units indexed: 10.

Direct internal dependency edges indexed: 18.

Directed internal dependency cycles indexed: 0.

`py.typed` is intentional package data, not an importable unit.

## Current dependency direction

```text
jsonl_viewer -> contracts
             -> engine -> contracts
                       -> _input -> _model
                       -> _model
                       -> _render -> contracts/_model/_json
                       -> _search -> _model/_json

_json -> _model

__main__ -> _standalone -> contracts/engine/_input
```

The facade and standalone entry points depend inward on the stable generic
contracts and transient engine. The engine depends on bounded parsing,
immutable private state, pure search, and deterministic rendering. Search and
rendering depend on shared strict bounded JSON expansion and immutable path
types, with separate call-local traversal budgets. Reusable internals never
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

- **Cursor/fold values:** Frozen `CursorPosition`, `FoldIdentity`,
  `VisibleCharacter`, and `ContainerMetadata` link visible display cells to
  source-record identity, structural paths, and parsed container delimiters.
  `ViewState` owns immutable session folds, cursor/preferred column, and a transient
  container-focus request. `RenderResult` carries effective cursor, visible cells,
  container facts, and geometry for subsequent context-sensitive input.

- **Source:** `src/jsonl_viewer/_model.py`
- **Responsibility:** Define immutable private snapshot and transient view-state
  values.
- **Supported surface:** No supported consumer API. The parser, engine, search,
  JSON expansion, and renderer share JSON value types and frozen record,
  snapshot, diagnostic, mode, search, prompt, view, and render-result values.
  Immutable `JSONPath` tuples distinguish string keys from integer array
  indices. Frozen `Occurrence` values retain record index, path, key/value or
  fallback kind, original-codepoint span, and matching text. `SearchState`
  holds ordered occurrences and the active index, with binary-search record
  membership/path access. Prompt values hold an uncommitted query or goto
  buffer, code-point cursor, and local validation error. `ViewState.reveal_match`
  requests focus positioning; `RenderResult.record_line_offset` reports the
  effective viewport offset for the engine to retain.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Defines values only; it owns no module
  state, lifecycle, or I/O.
- **Primary verification/documentation:** exercised through all engine, input,
  rendering, sample, and standalone tests; state behavior is documented in
  `docs/design-system.md`.

### `jsonl_viewer._json`

- **Source:** `src/jsonl_viewer/_json.py`
- **Responsibility:** Expand complete strict JSON containers encoded in strings
  under shared resource limits.
- **Supported surface:** No supported consumer API. Search and rendering use
  `expand_json_string`, `ExpansionBudget`, immutable `Expansion` and
  `SkippedExpansion` result wrappers, and the shared expansion constants.
- **Direct internal dependencies:** `_model`.
- **State, resources, and side effects:** Defines the 16-layer, 64-level,
  200,000-derived-node, and 16,777,216-cumulative-decoded-UTF-8-byte bounds. It
  updates only an explicitly supplied traversal budget and returns a whole
  accepted container, a content-safe skipped reason, or no expansion. Strict
  decoding rejects duplicate fields, non-finite values, malformed JSON,
  fragments, and scalar-only interpretations. Source strings remain intact;
  there is no cache, viewer state, I/O, or terminal ownership. Search and
  rendering instantiate independent budgets per record traversal; this module
  owns expansion acceptance, not traversal order or display projection.
- **Primary verification/documentation:** `tests/test_input_and_render.py`,
  `tests/test_public_api_and_engine.py`, `tests/test_unrestricted_search.py`,
  and the README input contract.

### `jsonl_viewer._render`

- **Cursor/folding projection:** Parsed container membership travels with JSON
  token segments through bounded expansion, search windows, and cell clipping.
  Nonempty folded containers retain opener/closer, keys, commas, and expansion
  cues; hidden children are still traversed to preserve expansion budgets and
  aggregate facts. Focus metadata excludes gutters, indentation, and display
  annotations. ANSI paints a readable inverse-video character cluster and bold
  matching delimiters; plain output reserves a caret row, intentionally reducing
  data capacity. At minimum height it retains one header, data row, caret, and
  status row. Search windows keep combining marks with their base character.

- **Source:** `src/jsonl_viewer/_render.py`
- **Responsibility:** Render deterministic bounded semantic ANSI/plain frames
  from call-local derived JSON projections.
- **Supported surface:** No supported consumer API. The engine consumes private
  loading/frame renderers, effective viewport offset, and current record-line count; tests use the
  private SGR-stripping verifier.
- **Direct internal dependencies:** `_json`, `_model`, and `contracts`.
- **State, resources, and side effects:** Owns immutable role/bound metadata,
  one call-local expansion budget, projection facts, and pure formatting
  behavior only. It
  performs no terminal or filesystem I/O. Rendering reorders/project fields by
  `ViewerSpec` and recursively traverses complete string-encoded JSON containers
  accepted by `_json`, without mutating source/search values. Shared
  layer/depth/node/decoded-byte bounds retain an over-bound candidate as its
  original string with a content-safe cue. Display paths include integer array
  indices; the active occurrence identifies the exact key/value span for SGR
  emphasis and printable `⟦…⟧` focus. Focused tokens use the original span plus
  at most 32 code points of context on either side, escaped after slicing;
  horizontal segment windows prioritize focus before final cell clipping.
  Raw, normalized, or unavailable decoded occurrences receive labeled bounded
  excerpts when no corresponding token is rendered. Focus-row metadata lets
  rendering resolve requested vertical positioning without terminal controls.
  Record markers retain selected/other membership without whole-line styling.
  Ordinary string-leaf preview bounds apply after expansion at complete UTF-8 code
  points; visible per-leaf markers and footer aggregates report expansion,
  skipped, and retained/full truncation facts without removing keys,
  containers, delimiters, or child presence. The same renderer formats
  multiline JSON, projects the supplied scope label, exact ID, and optional
  subject into the header, neutralizes controls, computes Unicode cell
  clipping, and emits optional SGR without cursor/lifecycle controls. Isolated
  surrogate leaves use visible escapes and a localized bounded preview fallback.
  Active
  search-query and goto editors occupy the two footer rows;
  bounded horizontal windows keep a printable logical cursor visible and
  display prompt-local validation errors without altering committed state.
- **Primary verification/documentation:** `tests/test_input_and_render.py`,
  `tests/test_unrestricted_search.py`, `tests/test_documented_samples.py`, and
  `docs/design-system.md`.

### `jsonl_viewer._search`

- **Source:** `src/jsonl_viewer/_search.py`
- **Responsibility:** Locate bounded full-text occurrences in complete JSON
  record values.
- **Supported surface:** No supported consumer API. The engine uses
  `find_matches` and transactional `SearchLimitError`; `MAX_OCCURRENCES`
  bounds successful results to 100,000 occurrences.
- **Direct internal dependencies:** `_json` and `_model`.
- **State, resources, and side effects:** Owns pure matching with call-local
  paths and one explicit expansion budget per record. Source-record order,
  insertion-order keys/children, and leaf offsets determine occurrence order.
  Literal Unicode-casefold matches map back to original-codepoint spans using
  a compact offset array; overlapping original spans are suppressed. Full keys,
  strings, and scalar text are searched independently of clipped frames.
  Strict encoded containers expand through `_json`; decoded children take
  precedence over raw encoding. Containers without child hits use compact
  normalized JSON fallback; encoded strings without decoded hits use raw
  fallback. Returned immutable occurrences distinguish these representations.
  Exceeding the occurrence limit raises instead of returning partial results.
  It owns no prompt, navigation,
  committed search state, source mutation, I/O, or terminal resource.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `tests/test_input_and_render.py`, `tests/test_unrestricted_search.py`,
  `README.md`, and `docs/design-system.md`.

### `jsonl_viewer._standalone`

- **Source:** `src/jsonl_viewer/_standalone.py`
- **Responsibility:** Adapt the generic viewer to one standalone CLI-owned
  terminal lifecycle.
- **Supported surface:** The installed `jsonl-viewer` entry point targets
  internal `main(argv=None)`. `_TerminalHost` and read/parser helpers are not
  supported library APIs. The CLI accepts the exact conversation/scope ID,
  scope label (default `Conversation`), and optional scope subject separately.
  Search is full-text only; there is no field-selection CLI option.
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
  subject—and configurable Simple-mode field identities. There is no
  `searchable_fields` constructor argument. `input_protocol` defaults
  to `semantic`, whose search event is `search<TAB>QUERY`; `keys` additionally enables bounded
  text/key/literal-line envelopes without changing the three-name facade or
  host method signatures. `ViewerHost` owns size/color decisions,
  complete-frame presentation, closed event delivery, and close restoration.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Defines and validates bounded immutable
  values/protocols only. Import and construction perform no I/O.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `README.md`, and `docs/design-system.md`.

### `jsonl_viewer.engine`

- **Cursor/fold transitions:** Main-view `h/l` move visible character cells,
  `j/k` preserve a preferred display column across visible data rows, and Enter
  toggles the innermost nonempty container. Arrow/semantic record navigation and
  paging retain their responsibilities. Successful viewport/mode changes reset
  focus; geometry changes clamp it. Folds are immutable record/path identities
  retained for this call, including nested folds beneath a folded parent. Search
  selection unfolds only containers hiding the hit and focuses its first display
  cell; later manual folding remains effective until search navigation. Batched
  text input refreshes structural geometry before subsequent cursor actions.

- **Source:** `src/jsonl_viewer/engine.py`
- **Responsibility:** Coordinate one transient read-only view through an
  injected host.
- **Supported surface:** Package-supported `view_jsonl(source, spec, host)`
  through the root facade. Its exact source is immutable `bytes`; it returns
  `None` and persists no view state.
- **Direct internal dependencies:** `_input`, `_model`, `_render`, `_search`,
  and `contracts`.
- **State, resources, and side effects:** Owns navigation, within-record paging,
  Simple/Verbose, committed full-text search, active occurrence, help, and
  transient message state only for one call. It delegates matching to `_search`,
  retaining prior committed search on an occurrence-limit failure. Next/previous
  wraps through individual occurrences and requests viewport focus positioning;
  manual record/page navigation disables automatic reveal. The effective
  renderer offset is retained in immutable view state. The active occurrence's
  hidden top-level path triggers Verbose promotion on initial search or
  next/previous selection; renderer visibility never limits matching.
  In `keys` mode it additionally owns all
  navigation bindings, ordinary-line command grammar, and search/goto prompt
  drafts, text editing, validation, and cancellation. Search opens one query
  draft; semantic `search<TAB>QUERY`, line `/ QUERY`, and alias `// QUERY`
  reach the same full-text action, preserving an entire multiword phrase.
  Field-bearing semantic search events are rejected. Event payloads are bounded
  independently of envelope prefixes at 8,192 characters; goto drafts
  are limited to 128 and query drafts to 1,024 code points. Raw controls cannot
  enter a draft. Logical cursor edits include arrows, Home/End, Backspace,
  Delete, Ctrl+A/E, prefix deletion with Ctrl+U, suffix deletion with Ctrl+K,
  and previous-word deletion with Ctrl+W. Prompt Escape discards only the draft
  and preserves committed view state, including prior messages and search
  position. EOF closes; interrupt closes an active prompt's view and otherwise
  follows main-view cancel precedence. Help and malformed/empty snapshots
  cannot begin prompts. Supported semantic events remain accepted in both input
  modes. It presents Loading before parsing, renders bounded snapshot/error
  states, and calls `host.close_view()` in `finally`. It performs no file,
  terminal-driver, network, persistence, replay, retry, or live-tail operation.
- **Primary verification/documentation:** `tests/test_public_api_and_engine.py`,
  `tests/test_input_and_render.py`, `tests/test_unrestricted_search.py`,
  `tests/test_documented_samples.py`, `README.md`, and `docs/design-system.md`.
