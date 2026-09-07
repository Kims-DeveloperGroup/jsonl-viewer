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
- **Responsibility:** Define immutable private snapshot and transient view-state
  values.
- **Supported surface:** No supported consumer API. The parser, engine, search,
  JSON expansion, and renderer share JSON value types and frozen record,
  snapshot, diagnostic, mode, search, prompt, view, and render-result values.
  Immutable `JSONPath` tuples distinguish string keys from integer array
  indices; `MatchPaths` and `SearchState.match_paths` align resolved paths with
  deduplicated record matches. `paths_for_record` also supports private legacy
  search-state values without explicit paths. Prompt values hold an uncommitted
  stage, buffer, code-point cursor, selected field/path, and local validation
  error.
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

- **Source:** `src/jsonl_viewer/_render.py`
- **Responsibility:** Render deterministic bounded semantic ANSI/plain frames
  from call-local derived JSON projections.
- **Supported surface:** No supported consumer API. The engine consumes private
  loading/frame renderers and the current record-line count; tests use the
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
  indices; supplied match paths determine line highlighting while record
  markers identify selected/other matches. Independent search/display
  traversal order, projection, expansion limits, or preview clipping can leave
  matched text absent from a displayed frame without removing its record marker.
  String-leaf preview bounds apply only after expansion at complete UTF-8 code
  points; visible per-leaf markers and footer aggregates report expansion,
  skipped, and retained/full truncation facts without removing keys,
  containers, delimiters, or child presence. The same renderer formats
  multiline JSON, projects the supplied scope label, exact ID, and optional
  subject into the header, neutralizes controls, computes Unicode cell
  clipping, and emits optional SGR without cursor/lifecycle controls. Isolated
  surrogate leaves use visible escapes and a localized bounded preview fallback.
  Active
  search-field, search-query, and goto editors occupy the two footer rows;
  bounded horizontal windows keep a printable logical cursor visible and
  display prompt-local validation errors without altering committed state.
- **Primary verification/documentation:** `tests/test_input_and_render.py`,
  `tests/test_unrestricted_search.py`, `tests/test_documented_samples.py`, and
  `docs/design-system.md`.

### `jsonl_viewer._search`

- **Source:** `src/jsonl_viewer/_search.py`
- **Responsibility:** Resolve bounded literal searches against complete JSON
  record values.
- **Supported surface:** No supported consumer API. The engine uses
  `parse_selector`, immutable `Selector`, content-safe `SelectorError`,
  `MAX_FIELD_CHARACTERS`, and `find_matches`.
- **Direct internal dependencies:** `_json` and `_model`.
- **State, resources, and side effects:** Owns selector syntax and pure matching
  with call-local paths and one explicit expansion budget per record. The
  128-character selector accepts dotted keys, nonnegative `[INDEX]` segments,
  or JSON Pointer with `~0`/`~1` escaping. Nonempty exact top-level keys take
  precedence over path interpretation, including slash-prefixed keys that
  shadow a Pointer. Blank field searches the whole record. Matching case-folds
  full keys, strings, and scalar text, with compact JSON container fallback;
  it never searches clipped frames. Selecting a string itself retains literal
  substring semantics, while deeper selectors and full text can traverse
  strict encoded containers through `_json`. Results contain deduplicated
  record indices and immutable resolved paths. It owns no prompt, navigation,
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
  Repeatable `--searchable-field` values are compatibility presets and do not
  restrict searches.
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
  subject—bounded ordered compatibility presets in the required
  `searchable_fields` constructor argument, and configurable Simple-mode field
  identities. Empty presets are valid and never restrict search. Its appended
  `input_protocol` field defaults
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
- **Direct internal dependencies:** `_input`, `_model`, `_render`, `_search`,
  and `contracts`.
- **State, resources, and side effects:** Owns navigation, within-record paging,
  Simple/Verbose, committed field/full-text search, current result, help, and
  transient message state only for one call. It delegates selector validation
  and matching to `_search`, then carries resolved paths in immutable search
  state. Hidden top-level match paths trigger Verbose promotion on initial
  search or next/previous selection; renderer visibility never limits matching.
  In `keys` mode it additionally owns all
  navigation bindings, ordinary-line command grammar, and search/goto prompt
  stages, text editing, validation, and cancellation. A blank search field
  commits full-text search; semantic `search<TAB><TAB>QUERY`, line `// QUERY`,
  and single-token `/ QUERY` reach the same action. Legacy `/ FIELD QUERY` and
  semantic field searches remain accepted. Event payloads are bounded
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
  `tests/test_input_and_render.py`, `tests/test_unrestricted_search.py`,
  `tests/test_documented_samples.py`, `README.md`, and `docs/design-system.md`.
