# Python Modularity Policy

## Authority and scope

This repository-owned policy governs Python package and module boundaries in
`jsonl-viewer`. It applies when work adds, removes, renames, moves, splits, or
merges an importable unit; changes responsibility, dependency direction,
public imports or exports, shared abstractions, resource ownership, or
side-effect lifecycles; or makes a structural compatibility decision.

The terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
normative. A `MUST` exception requires an explicit repository decision that
identifies the waived rule, reason, affected consumers, containment, tests, and
removal condition.

The separate package contract is strict: the library MUST remain generic,
read-only, transient, standard-library-only at runtime, and independent of any
embedding application. In particular, production code MUST NOT import
`story_writing_agents` or encode its commands, storage paths, schemas, or
terminal lifecycle.

## Canonical Python module index

The current implementation inventory is `<PROJECT_ROOT>/PYTHON_MODULE_INDEX.md`.
It is descriptive current-state navigation, not a plan or history.

- Every importable production package and module under a declared source root
  MUST appear exactly once. Tests, caches, generated files, and non-importable
  package data are excluded.
- The index MUST record the source root, exact module/edge/cycle counts, current
  dependency direction, and one entry per unit.
- Every entry MUST name its import path, source path, one cohesive
  responsibility, supported public imports or entry points, direct internal
  dependencies, owned state/resources and side effects, and primary tests or
  documentation.
- Supported surfaces MUST be derived from package `__all__`, documented import
  paths, and active build entry points. A test-only direct import is not public.
- Adding or removing a unit or materially changing responsibility, exports,
  dependencies, ownership, entry points, or side effects requires a matching
  index update in the same complete change.
- Review MUST compare every source path with the index and verify affected
  semantic entries against implementation, imports, tests, packaging, and
  documentation. Implementation is observed truth when a mismatch exists, but
  the change is incomplete until reconciled.

## Responsibility and cohesion

- Each module MUST have one responsibility expressible without joining
  unrelated capabilities with “and.”
- Behavior, contracts, tests, and state that change for the same product reason
  SHOULD stay together; independently changing concerns SHOULD be separated.
- A module MUST NOT become a generic `utils`, `helpers`, `common`, or `misc`
  dumping ground. Shared modules need a domain-specific name and owner.
- A split SHOULD occur when consumers need independent subsets, optional
  infrastructure leaks into core imports, resource ownership is hidden, or
  dependency direction becomes unclear. Line count alone is not a reason.
- A merge SHOULD occur when units have no independent responsibility, are only
  forwarding layers, or always change together without reducing coupling.

## Dependency direction and isolation

- Dependencies MUST be explicit through imports, parameters, immutable values,
  or declared protocols. Behavior MUST NOT depend on import order.
- Runtime import cycles are prohibited. Type-only or local imports MUST NOT
  conceal an architectural cycle.
- Entry points and concrete terminal adapters MAY depend on the public engine;
  the engine MAY depend on stable contracts, bounded input, state values, and
  pure rendering. Reusable contracts/model/rendering MUST NOT depend back on
  entry points or terminal owners.
- The public engine MUST interact with terminals only through `ViewerHost`.
  When a host is injected, the package MUST NOT assume ownership of raw mode,
  alternate screen, cursor, signals, geometry, input, output, or cleanup.
- The standalone adapter MAY own those resources only around its own CLI call
  and MUST restore them visibly.
- Production modules MUST NOT reach into underscored names owned by another
  package, mutate peer internals, modify `sys.path`, or use wildcard imports.
- Dynamic imports are allowed only for a documented platform extension point;
  platform-optional standard-library imports must fail to an explicit fallback.

## Public API and compatibility

- The supported library facade is intentional and minimal. Package `__all__`
  MUST match documented root imports exactly.
- Public dataclasses and values MUST be immutable where the contract promises
  immutability. Public protocols MUST describe lifecycle and failure ownership.
- Public signatures, accepted types/domains, event strings, render-frame
  semantics, exception behavior, and observable close behavior are
  compatibility contracts.
- Internal modules MAY be imported by focused tests but MUST NOT be documented
  as supported consumer paths.
- A breaking change requires explicit authorization, updated consumers and
  documentation, migration guidance, and compatibility tests in the same
  complete change.
- A facade MUST add a stable ownership boundary; it MUST NOT mirror every
  internal name for convenience.

## State, side effects, and resources

- Importing reusable modules MUST NOT read or write files, access a network,
  authenticate, launch subprocesses, alter terminal modes, register signals,
  create threads, or start the application.
- Parsing and rendering MUST operate only on explicit immutable inputs and MUST
  remain deterministic apart from host-supplied geometry/color decisions.
- Filesystem and terminal resources MUST have one owner with visible
  acquisition, restoration, and failure behavior.
- The library MUST expose no filesystem writer, live-tail, edit, replay, retry,
  persistence, or network API.
- Navigation, mode, search, help, diagnostic, and viewport state MUST be scoped
  to one `view_jsonl` call and discarded on close.
- The source snapshot MUST remain immutable; failure and cancellation MUST
  still return terminal ownership through `close_view`.
- Bounded parsing, query size, rendering geometry, string previews, and
  content-safe diagnostics are part of the resource and security boundary.

## Source, packaging, and dependencies

- Production packages and modules MUST stay under the source root declared by
  `pyproject.toml`; importable directories require `__init__.py` unless an
  explicitly authorized namespace package is introduced.
- `pyproject.toml` is authoritative for discovery, entry points, Python
  versions, package data, license, and dependencies.
- Runtime and development dependencies MUST remain empty unless a separate
  explicit decision authorizes one after standard-library alternatives,
  maintenance, security, license, size, and Python compatibility are reviewed.
- Build-system tooling is packaging infrastructure and MUST NOT leak into
  runtime imports.
- Modules MUST import successfully in a clean Python 3.11+ interpreter from an
  installed artifact without checkout-relative path assumptions.
- Package data MUST be declared intentionally. Wheels and source archives MUST
  contain the license, documentation promised by metadata, source package, and
  typing marker, without caches, tests-as-runtime, or unrelated workspace data.

## Rendering boundary

- ANSI frames MAY contain semantic SGR sequences only. Cursor movement,
  alternate-screen, erase, mouse, clipboard, title, hyperlink, and raw-mode
  controls belong to a terminal-owning host.
- Plain and ANSI rendering MUST have exactly equivalent semantic text after SGR
  removal. Color MUST NOT be the only state signal.
- Provider/source-controlled text MUST be safely neutralized before terminal
  rendering. Unicode cell clipping and truncation markers MUST be deterministic
  and source-preserving.
- Search MUST use bounded literal substring matching for full-text or arbitrary
  field/path queries; an arbitrary expression/evaluation language is prohibited.

## Verification and change workflow

Before structural implementation:

1. classify the modularity and repository-planning gates;
2. inventory current modules, imports, public consumers, resources, side
   effects, tests, documentation, and packaging;
3. describe intended responsibilities, dependency direction, compatibility,
   ownership, and the exact index delta; and
4. stop for an unapproved breaking API, dependency, persistence, terminal
   ownership, or cross-repository decision.

Before completion:

1. import every affected module through supported paths in a clean process;
2. verify whole-root source/index parity, direct edges, cycles, private
   reach-through, unintended exports, and Story isolation;
3. run warning-strict standard-library tests, source immutability, ANSI/plain
   parity, control neutralization, geometry, malformed/bounded input, search,
   navigation, close cleanup, deterministic documented samples, and standalone
   smoke checks;
4. compile all sources, build distributable artifacts without network access,
   and inspect their contents and metadata; and
5. review the complete diff and report public compatibility, index consistency,
   checks, exceptions, blockers, and the next owner.

Tests MUST NOT become production dependencies. New or changed modules require
focused behavior and boundary tests. A missing or stale module-index entry,
cycle, Story import, terminal-ownership inversion, undeclared dependency, or
undocumented public export is a policy violation.
