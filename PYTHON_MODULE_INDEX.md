# Python Module Index

## Scope and supported boundaries

Source root: `src/`. Package root: `src/jsonl_viewer/`.
Importable production units indexed: 24.
Direct internal dependency edges indexed: 67.
Directed internal dependency cycles indexed: 0.
`py.typed` is package data, not an importable unit.

The index is current subsystem navigation. Packaging and source imports are
physical-membership authority; the repository modularity policy governs changes.
The runtime is standard-library-only, generic, read-only and transient. It has no
embedding-application model, preset detection, executable metadata or network API.

The supported root facade exports exactly `ViewerSpec`, `ViewerHost`,
`ViewerTerminal`, `view_jsonl(source, spec, host=None, *, terminal=None,
transient_only=False)`. The intentional public `jsonl_viewer.inspection` module
exports immutable Context, Group, Section, Facet, Option, Inspection, SearchScope,
Target, DecodeJson/DECODE and MissingValue/MISSING. Other module imports used by
tests are internal, not additional consumer APIs.

## Dependency direction

Facade and CLI depend inward on contracts and host-driven engines. Contracts and
models never depend back on controllers or terminal owners. Generic descriptors
contain inert values only. There are no runtime or concealed type-only cycles.
The following compact table records direct edges, including explicit local imports.

| Module | Direct internal dependencies |
| --- | --- |
| `jsonl_viewer` | `_terminal`, `contracts`, `engine` |
| `__main__` | `_standalone` |
| `_browser` | `_browser_render`, `_catalog`, `_controls`, `_model`, `_mouse`, `_query`, `_render`, `_search`, `_selection`, `contracts` |
| `_browser_render` | `_catalog`, `_cell_layout`, `_model`, `_render`, `_selection`, `contracts` |
| `_catalog` | `_json`, `_model`, `inspection` |
| `_cell_layout` | None |
| `_clipboard` | None |
| `_controls` | `_model`, `_mouse`, `_render`, `_search`, `_selection`, `contracts` |
| `_input` | `_model` |
| `_input_sources` | None |
| `_input_sources_linux` | `_input_sources` |
| `_input_sources_macos` | `_input_sources` |
| `_json` | `_model` |
| `_model` | None |
| `_mouse` | `_model` |
| `_query` | `_catalog`, `_model`, `_search`, `inspection` |
| `_render` | `_cell_layout`, `_json`, `_model`, `_selection`, `contracts` |
| `_search` | `_json`, `_model`, `inspection` |
| `_selection` | `_cell_layout`, `_model` |
| `_standalone` | `jsonl_viewer`, `_input`, `contracts` |
| `_terminal` | `_cell_layout`, `_clipboard`, `_input_sources`, `_input_sources_linux`, `_input_sources_macos`, `_mouse`, `contracts`, `engine` |
| `contracts` | `inspection` |
| `engine` | `_browser`, `_controls`, `_input`, `_model`, `_render`, `_selection`, `contracts` |
| `inspection` | None |

## Public values and view state

This subsystem owns immutable API/data shape and compatibility conversion, not
application semantics or terminal acquisition. Generic views need no identity
headers. Historical populated headers without Inspection keep their old record
projection; explicit Inspection opts into full-record browsing. Header conversion
produces generic Context before rendering. Descriptor bounds and typed Missing
remain independent of source data and do not execute caller code.

### `jsonl_viewer`

Source: `src/jsonl_viewer/__init__.py`. The four-export facade chooses an injected
host or a lazy terminal-owned adapter; importing it performs no terminal work.

### `jsonl_viewer.contracts`

Source: `src/jsonl_viewer/contracts.py`. Owns ViewerSpec, ViewerTerminal and the
five-method ViewerHost protocol, optional input-mode, clipboard-acknowledgement
and autoscroll notifications, and legacy header-to-Context conversion. Streams
remain caller-owned; existing public constructors and required methods are unchanged.

### `jsonl_viewer.inspection`

Source: `src/jsonl_viewer/inspection.py`. Public frozen descriptors validate
bounded tuple/scalar/reference shapes and incremental metadata size without I/O.

### `jsonl_viewer._model`

Source: `src/jsonl_viewer/_model.py`. Owns immutable snapshots, occurrences,
logical cursor/fold/selection state, pending copy requests, query results and renderer hit geometry.
Selection edge intent is distinct from a changed logical endpoint. BodyLine/PaintedSpan
retain only actual painted copyable segment ranges, never a document character map. BrowserState
owns invocation-only focus/layout, committed/draft conditions, bounded literal name-finding,
selected-info scroll and palette preference. Its 32-item navigation LRU retains section/cursor/
fold/viewport state without prompts, clipboard requests, drags or source copies; no persistence.

## Bounded source and selection

Parsing accepts every JSON root, with strict UTF-8/JSON and content-safe bounded
failures. Source bytes and physical RecordRef identities stay unchanged. Bounded
string expansion is shared by rendering and matching; caches and traversal budgets
have explicit per-record/per-view owners. Typed selectors are data, not a query
language. Query matching retains literal Unicode spans and source order.

### `jsonl_viewer._input`

Source: `src/jsonl_viewer/_input.py`. Parses immutable bytes into bounded records;
rejects malformed, duplicate-key, nonfinite or over-limit input with fixed diagnostics.

### `jsonl_viewer._json`

Source: `src/jsonl_viewer/_json.py`. Owns strict encoded-container expansion and
caller-supplied ExpansionBudget accounting; whole candidates are accepted or skipped.

### `jsonl_viewer._catalog`

Source: `src/jsonl_viewer/_catalog.py`. Its private Item separates stable internal identity
from the caller's opaque display/search ID; ungrouped IDs retain source-line identity. Binds original references to generic groups,
facets and cached selector previews. Original typed targets and declared-unavailable
metadata remain separate from eager diagnostics; unsupported automatic facets
produce notices without preventing ordinary source viewing.

### `jsonl_viewer._search`

Source: `src/jsonl_viewer/_search.py`. Owns bounded literal occurrence matching.
Scoped tries share fresh original-record budgets and selector-addressable decode
caches, preserving depth, representation and source order without a ranking decode.

### `jsonl_viewer._query`

Source: `src/jsonl_viewer/_query.py`. Purely computes typed facet OR/AND and scoped
candidate results. Selected-scope coverage is recomputed; failure never mutates
committed conditions or substitutes rewritten source.

## Presentation and interaction

Rendering emits printable frames and optional semantic SGR only; terminal escape
lifecycle belongs below. JSON geometry preserves original record/line identities,
Unicode clusters and bounded preview facts. Supplied renderer sessions own bounded
projection caches; transient controls own no host resources.

Generic inspection starts in Single/detail at the first retained member's containing
section. Single paints only its focused full-width pane. Optional Split keeps the
fixed wide list/detail geometry; compact fallback never changes stored preference.
Focus/layout/resize preserve meaningful navigation and committed conditions. Input
uses literal modal editing, explicit draft Apply/Cancel, renderer-owned mouse regions
and main-only layout switching; all state is discarded when the view closes.

### `jsonl_viewer._cell_layout`

Source: `src/jsonl_viewer/_cell_layout.py`. Owns bounded Unicode display-cell layout,
cluster windows and nearest-cell seeking; no terminal or source acquisition.

### `jsonl_viewer._render`

Source: `src/jsonl_viewer/_render.py`. Owns JSON projections, semantic/plain record
frames and bounded RenderSession caches. Its visible-index/chrome pane boundary
keeps the original snapshot, identities, cursor and cumulative line geometry.
Body-relative sparse selection hits and cached row character prefixes support
copy extraction and live counts without a document-wide character map. Painted
spans exclude viewport ellipses and partial-wide-glyph padding; composed pane
clipping trims paint/hit metadata while retaining logical navigation candidates.
RenderSession additionally owns one current projection-context scalar count cache:
at most one total per snapshot record (10,000 at the input bound) and four boundary
prefixes, independent of full-projection LRU eviction. Mode, folds and active search
occurrence changes clear that metadata; spec/snapshot rebinding also clears it.
No historical query contexts or extra projection rows are retained there. Search
brackets are presentation-only; existing bounded projection cues remain copyable.

### `jsonl_viewer._mouse`

Source: `src/jsonl_viewer/_mouse.py`. Validates bounded mouse envelopes and resolves
reported cells against renderer geometry, including left motion and release;
it performs no terminal-mode changes.

### `jsonl_viewer._selection`

Source: `src/jsonl_viewer/_selection.py`. Owns pure inclusive cluster ranges,
body-relative ordering, sparse boundary counting, projected-text extraction and
selection notices. It consumes immutable rows and owns no terminal, clipboard,
timer or source resource. Tests cover mouse ranges, Unicode and bounded copying;
README and design-system document the interaction.

### `jsonl_viewer._controls`

Source: `src/jsonl_viewer/_controls.py`. Applies validated JSON cursor, page, fold,
sibling, prompt and captured-drag transitions using explicitly supplied state/render
caches. Out-of-body drag intent can start edge scrolling before an endpoint changes;
each actual motion performs at most one step, and idle events continue stationary
scrolling in the frozen displayed-document order. Intermediate probes retain the
outer viewport's painted bounds before resolving endpoints.

### `jsonl_viewer._browser_render`

Source: `src/jsonl_viewer/_browser_render.py`. Owns generic chrome, single-row list cells,
Unicode name/ID highlighting, fixed selected-info scrolling, Dark/Light/ANSI roles, modals,
effective Single/Split composition and painted hit regions. Pure name finding projects
materialized query results without changing them; layout remains state data.

### `jsonl_viewer._browser`

Source: `src/jsonl_viewer/_browser.py`. Coordinates generic input, drafts, literal name finding,
per-view focus/layout/palette, bounded group-navigation restoration and retained cursor
viewport acceptance through ViewerHost. Applied body conditions invalidate saved navigation;
name finding only narrows the list. Detail record movement stays within an explicit group;
ungrouped records retain ordinary eligible-source traversal.
It captures detail drags ahead of chrome/list routing and dispatches each released
copy request once through optional host capabilities with truthful outcome notices.
It consumes prepared data only, does not
acquire or close the host, and skips source-bound metadata on malformed input.

### `jsonl_viewer.engine`

Source: `src/jsonl_viewer/engine.py`. Owns one source parse and the once-only outer
host-close boundary. It dispatches legacy record viewing or generic inspection;
legacy viewing dispatches released copy requests and autoscroll idle transitions.
Controllers and rendering do not acquire terminal ownership.

## Terminal and desktop input-source ownership

Only an explicit owned-terminal call acquires raw mode, alternate screen, mouse
reporting, cursor/signal restoration and desktop input-source state. Injected hosts
keep those resources. Native desktop integration is bounded, best-effort and lazy;
imports do not authenticate, launch helpers or alter terminal/input-source state.

### `jsonl_viewer._terminal`

Source: `src/jsonl_viewer/_terminal.py`. Owns explicit terminal acquisition, bounded
physical decoding, event-triggered command-source recovery, presentation and visible cleanup. Button-event mouse tracking
reports drag motion; optional edge polling uses 50 ms while ordinary idle remains
500 ms and escape decoding retains its separate deadline. Caller streams stay open.

### `jsonl_viewer._clipboard`

Source: `src/jsonl_viewer/_clipboard.py`. Owns explicit local UTF-8 clipboard writes
through macOS pbcopy or available Linux wl-copy/xclip helpers. Fixed arguments,
stdin, suppressed output and a two-second timeout bound helper execution; imports
perform no helper work. Successful helper exit acknowledges acceptance, unavailable
or failed transports never claim success, and SSH/tmux forwarding is excluded.
Native-adapter tests mock helpers; README documents local clipboard support.

### `jsonl_viewer._input_sources`

Source: `src/jsonl_viewer/_input_sources.py`. Owns the explicit keyboard-source
lifecycle, mode transitions, verified command recovery and exact-entry restoration
through a generic adapter protocol. ASCII input, search text and idle never poll.

### `jsonl_viewer._input_sources_macos`

Source: `src/jsonl_viewer/_input_sources_macos.py`. Owns lazy native macOS source
selection/restoration behind the desktop adapter; no import-time native resource work.

### `jsonl_viewer._input_sources_linux`

Source: `src/jsonl_viewer/_input_sources_linux.py`. Owns bounded Linux framework
selection, helper execution and restoration behind the desktop adapter.

## Command entry points

### `jsonl_viewer._standalone`

Source: `src/jsonl_viewer/_standalone.py`. Adapts CLI arguments and explicit bounded
file/stdin input to the public facade. Optional legacy header flags preserve their
compatibility meaning; header-free input enters generic inspection.

### `jsonl_viewer.__main__`

Source: `src/jsonl_viewer/__main__.py`. Executable-only module delegates to the CLI
adapter and exits; ordinary package import does not execute it.

## Verification owners

Public API/engine and packaging tests cover facade, discovery, signatures and
artifacts. Inspection contracts/query/UI/standalone and review-regression cases cover
generic data, scope accounting, typed values, modal transactions, layout and source
fidelity. Existing cursor/folding/navigation/cache, cell-layout, mouse, terminal,
input-source and documented-sample tests cover their respective owners. README,
`docs/inspection.md`, design system and generic samples document supported behavior;
tests remain outside runtime dependencies. Build metadata and entry points remain
in `pyproject.toml`; this index introduces no additional public imports.
