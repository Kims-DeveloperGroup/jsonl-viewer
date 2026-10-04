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

- **Source:** `src/jsonl_viewer/_model.py`
- **Responsibility:** Immutable private snapshot, search, prompt, and view values.
- **Supported surface:** Internal only. Record/path identities distinguish object
  keys and array indices. Container/property metadata associates visible grapheme
  cells and key anchors with parsed structure, never string-delimiter heuristics.
- **Direct internal dependencies:** None.
- **State/resources:** `ViewState` owns session folds, cursor/preferred column,
  container focus, retained property-key reveal, and search/prompt state.
  `RenderResult` owns bounded visible geometry, full-record navigable row indices, sibling metadata, and deterministic
  visible/idle frame text. No module state, I/O, or lifecycle ownership.
- **Verification:** Engine, cursor/folding, rendering, search, and sample tests;
  `docs/design-system.md` describes the behavior.

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

- **Sibling/blink projection:** Immutable property ownership and first-key-cell
  anchors survive clipping and encoded-JSON expansion. Complete bounded record
  projections provide siblings beyond the viewport. Each render paints visible
  and idle variants from the same projection; matching delimiters remain steady.
- **Cursor/folding projection:** Parsed container membership travels with JSON
  token segments through bounded expansion, search windows, and cell clipping.
  Nonempty folded containers retain opener/closer, keys, commas, and expansion
  cues; hidden children are still traversed to preserve expansion budgets and
  aggregate facts. Focus metadata excludes gutters, indentation, and display
  annotations. ANSI paints a readable inverse-video character cluster and
  bold inverse matching delimiters, with underline for cursor overlap. Folded placeholders
  use full-contrast `{...}` / `[...]`; plain output reserves a caret row,
  intentionally reducing data capacity. At minimum height it retains one header, data row, caret, and
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

- **Idle transport:** Raw input polls every 500 ms, returning semantic `idle`
  without finalizing the incremental UTF-8 decoder. Ordinary-line transport
  remains blocking/static; bounded escape parsing keeps its separate deadline.

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

- **Source:** `src/jsonl_viewer/engine.py`
- **Responsibility:** Coordinate one transient read-only view through the host. Reveal adjacent cursor pages using bounded row metadata and constant render probes; page commands land on the last/first row according to direction.
- **Supported surface:** Root-facade `view_jsonl(source, spec, host)` accepts exact
  immutable bytes, returns None, and persists no view state.
- **Direct internal dependencies:** `_input`, `_model`, `_render`, `_search`,
  `contracts`.
- **State/resources:** Main-view character movement, record/page navigation,
  structural folding, sibling-property navigation, search and prompt editing stay
  engine-owned. `J/K` cycle sibling identities in rendered property order, focus the key,
  and report endpoint notices; folds and occurrence counts remain unchanged.
  Up/Down cycle source records, reset viewport/cursor, and report first/last/only
  record notices; paging scrolls within a record before using that transition. Hidden
  folded descendants and Simple-mode exclusions are not navigation targets.
- **Blink ownership:** Hosts deliver 500-ms `idle` events. The engine alternates
  cached frame variants without rebuilding projections. Real input restores the
  cursor; geometry/color changes invalidate the cache. Prompt/help views remain
  steady. Hosts without idle events retain static emphasis.
- **Compatibility:** Three public exports and five host signatures stay stable.
  Existing semantic events, prompt literal input, bounded transport validation,
  search-driven ancestor unfolding, and immutable source handling remain intact.
  Batched text refreshes structural geometry before subsequent navigation.
- **Lifecycle:** Loading precedes parsing; host closure is guaranteed in finally.
  No terminal driver, filesystem, thread, network, replay, or live-tail ownership.
- **Verification:** Public API/engine, cursor/folding, search, deterministic sample,
  and standalone integration tests; README and design-system behavior contract.
