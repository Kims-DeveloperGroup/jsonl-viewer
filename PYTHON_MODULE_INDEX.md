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

Importable production units indexed: 12.

Direct internal dependency edges indexed: 24.

Directed internal dependency cycles indexed: 0.

`py.typed` is intentional package data, not an importable unit.

## Current dependency direction

```text
jsonl_viewer -> contracts
             -> engine -> contracts/_input/_mouse/_model/_render/_search
             -> _terminal -> contracts/engine/_mouse

_mouse -> _model
_render -> contracts/_model/_json
_search -> _model/_json
_json -> _model
_input -> _model

__main__ -> _standalone -> jsonl_viewer/contracts/_input
```

The facade and standalone entry points depend inward on the stable generic
contracts and transient engine. The engine depends on bounded parsing,
immutable private state, pure search, and deterministic rendering. Search and
rendering depend on shared strict bounded JSON expansion and immutable path
types, with separate call-local traversal budgets. Pure engine/model/contracts/render
modules never depend on the terminal owner; the facade imports it lazily only
for an owned call. The graph is acyclic and no production
unit imports Story or any third-party runtime package.

## Package and module entries

### `jsonl_viewer`

- **Source:** `src/jsonl_viewer/__init__.py`
- **Responsibility:** Provide the stable library facade and select a borrowed
  host or one viewer-owned terminal call.
- **Supported surface:** `ViewerHost`, `ViewerSpec`, `ViewerTerminal`, and
  `view_jsonl(source, spec, host=None, *, terminal=None, transient_only=False)`.
  Existing supplied-host calls retain their lifecycle and return None. A host
  cannot be combined with a terminal or `transient_only=True`.
- **Direct internal dependencies:** `contracts`, `engine`, and lazily `_terminal`.
- **State/resources:** Validates source/spec and ownership arguments before I/O.
  Import and borrowed-host calls perform no process-stdio or terminal work.
- **Verification:** Public API/engine and standalone/packaging tests; README.

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
- **State/resources:** `ViewState` owns session folds, logical cursor/preferred
  column, horizontal body offset, one-shot cursor reveal, container/property
  focus, and search/prompt state. `VisibleCharacter` separates logical display
  cells from painted screen columns and full-cluster screen widths. `RenderResult` owns bounded visible
  geometry, compact logical navigation candidates for viewport rows,
  full-record navigable row indices, horizontal bounds, sibling metadata, and
  deterministic visible/idle frame text. No module state, I/O, or lifecycle ownership.
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
  anchors survive horizontal slicing and encoded-JSON expansion. Complete bounded record
  projections provide siblings and navigable row indices beyond the viewport.
  Root annotations reveal a navigable JSON cell. Each render paints visible
  and idle variants from the same projection; matching delimiters remain steady.
- **Cursor/folding projection:** Parsed container membership travels with JSON
  token segments through bounded expansion, search excerpts, and cell slicing.
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
  horizontal reveal brings the complete focused keyword into the body viewport
  when it fits. Oversized focus retains a marked bounded prefix; panes too
  narrow for brackets plus one cluster prioritize a readable glyph over those
  annotations and a clipping ellipsis. Fixed gutters and footer overflow cues remain.
  Raw, normalized, or unavailable decoded occurrences receive labeled bounded
  excerpts when no corresponding token is rendered. Focus-row metadata lets
  rendering resolve requested vertical positioning without terminal controls.
  Record markers retain selected/other membership without whole-line styling.
  Ordinary string-leaf preview bounds apply after expansion at complete UTF-8 code
  points; visible per-leaf markers and footer aggregates report expansion,
  skipped, and retained/full truncation facts without removing keys,
  containers, delimiters, or child presence. The same renderer formats
  multiline JSON, projects the supplied scope label, exact ID, and optional
  subject into the header and neutralizes controls. Complete Unicode clusters
  survive horizontal slicing with their roles and structural metadata. One
  horizontal offset spans all JSON bodies in the vertical pane and clamps to
  its widest projected row; gutters and chrome stay fixed. Compact navigation
  candidates retain row endpoints, cursor neighbors, preferred-column cells,
  and focus anchors without allocating a whole-record character map. The footer
  reports horizontal position and left/right overflow. It emits optional SGR
  without cursor/lifecycle controls. Isolated
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
- **Responsibility:** Adapt CLI arguments and one bounded binary source snapshot
  to the public viewer facade.
- **Supported surface:** Installed `jsonl-viewer` targets private `main(argv=None)`.
  Source, generic header fields, and `--no-color` retain existing CLI grammar.
- **Direct internal dependencies:** `jsonl_viewer`, `_input`, and `contracts`.
- **State/resources:** Owns only CLI-opened source and EOF-fallback handles,
  closing them after reading/viewing. Uses `ViewerTerminal` transport; modes,
  decoding, geometry, and restoration belong to `_terminal`. No import-time I/O.
- **Verification:** Standalone/packaging tests; README.

### `jsonl_viewer._terminal`

- **Source:** `src/jsonl_viewer/_terminal.py`
- **Responsibility:** Own one terminal lifecycle and bounded physical input
  transport for standalone or library-owned viewing.
- **Supported surface:** None; facade uses private `view_owned`.
- **Direct internal dependencies:** `_mouse`, `contracts`, and `engine`.
- **State/resources:** Owns no-flush cbreak/echo suppression, saved current termios,
  alternate screen, cursor visibility, SGR tracking, output-stream geometry, and
  cleanup. Enables 1006 before 1000; disables 1000 before 1006. Every managed exit
  attempts display, attributes, and scoped default-only SIGTERM/SIGHUP restoration;
  custom/ignored handlers and SIGINT remain untouched. Required default-signal
  ownership fails off the main thread before tty mutation. The first default
  termination unwinds, later owned terminations cannot interrupt cleanup, and
  successful cleanup restores dispositions before re-delivering the original
  signal with its default semantics. Streams remain caller-owned.
  Buffered character callbacks receive readiness checks only when new bytes are
  needed. Default UTF-8 decoding persists across 500-ms idle and 20-ms escape
  deadlines. Recognized CSI prefixes persist inertly across deadlines until
  completed or invalidated, preserving prompt drafts and subsequent keyboard
  characters; bare Escape retains cancellation. Incomplete/invalid mouse reports
  stay inert. Unsupported terminals use bounded line
  fallback unless transient-only ownership fails before rendering.
- **Verification:** Terminal ownership, physical mouse, standalone/packaging tests.

### `jsonl_viewer._mouse`

- **Source:** `src/jsonl_viewer/_mouse.py`
- **Responsibility:** Validate bounded mouse reports and resolve painted JSON hits.
- **Supported surface:** None; pure private parsing and hit testing.
- **Direct internal dependencies:** `_model`.
- **State/resources:** Immutable reports with decimal button 0..255, positive
  one-based coordinates of at most six digits, and press/release phase.
  Modifier-independent left presses and wheel-up/down are recognized; other
  buttons, motion, releases, and invalid geometry are inert. Hit testing uses
  renderer-owned absolute rows, columns, and cluster widths without projection
  or terminal I/O.
- **Verification:** Mouse parsing, Unicode/panned/caret hit tests, engine tests.

### `jsonl_viewer.contracts`

- **Source:** `src/jsonl_viewer/contracts.py`
- **Responsibility:** Define immutable generic embedding contracts.
- **Supported surface:** Frozen `ViewerSpec`, frozen `ViewerTerminal`, and the
  five-method structural `ViewerHost` through the root facade. Spec keeps bounded
  generic headers, primary fields, and semantic/keys protocol. Keys additionally
  accepts bounded mouse envelopes. The terminal adapter supplies input/output
  streams, optional buffered character reader, and strict boolean `no_color`.
- **Direct internal dependencies:** None.
- **State/resources:** Validates values/transport shape without I/O. A host retains
  complete terminal lifecycle ownership; a terminal adapter transfers one view's
  lifecycle while its streams remain caller-owned and are never closed.
- **Verification:** Public API, host ownership, terminal transport tests; README.

### `jsonl_viewer.engine`

- **Source:** `src/jsonl_viewer/engine.py`
- **Responsibility:** Coordinate one transient read-only view through the host.
  Reveal adjacent cursor pages using bounded row metadata and constant render
  probes; page commands land on the last/first row according to direction.
- **Supported surface:** Private host-engine `view_jsonl(source, spec, host)`
  accepts exact immutable bytes, returns None, and persists no view state.
- **Direct internal dependencies:** `_input`, `_mouse`, `_model`, `_render`, `_search`,
  `contracts`.
- **State/resources:** Main-view character movement, record/page navigation,
  structural folding, sibling-property navigation, search and prompt editing stay
  engine-owned. Left/Right pan by half the body width; `h/l` cross complete
  projected rows and reveal logical destinations before crossing pages.
  Record selection, goto, and mode reset horizontal position; within-record
  paging and resize preserve/clamp it. Panning relocates an offscreen cursor
  without reversing the pan, and search/key/fold navigation reveals its target.
  `J/K` cycle sibling identities in rendered property order, focus the key,
  and report endpoint notices; folds and occurrence counts remain unchanged.
  Up/Down cycle source records, reset viewport/cursor, and report first/last/only
  record notices; paging scrolls within a record before using that transition. Hidden
  folded descendants and Simple-mode exclusions are not navigation targets.
- **Blink ownership:** Hosts deliver 500-ms `idle` events. The engine alternates
  cached frame variants without rebuilding projections. Real input restores the
  cursor; geometry/color changes invalidate the cache. Prompt/help views remain
  steady. Hosts without idle events retain static emphasis.
- **Mouse behavior:** Keys-protocol wheel presses reuse cursor-up/down; left
  presses update the logical cursor using the last painted full-cluster cell
  without folding. Prompts, help, malformed snapshots, and invalid reports ignore
  mouse input. Existing keyboard and semantic behavior remain intact.
- **Compatibility:** Five host signatures stay stable.
  Existing semantic events, prompt literal input, bounded transport validation,
  search-driven ancestor unfolding, and immutable source handling remain intact.
  Batched text refreshes logical/horizontal geometry before subsequent navigation.
- **Lifecycle:** Loading precedes parsing; host closure is guaranteed in finally.
  An active failure takes precedence over secondary closure failures.
  No terminal driver, filesystem, thread, network, replay, or live-tail ownership.
- **Verification:** Public API/engine, cursor/folding, search, deterministic sample,
  and standalone integration tests; README and design-system behavior contract.
