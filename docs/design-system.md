# JSONL Viewer Terminal Design System

This document is the repository-owned visual and interaction contract for
`jsonl-viewer`. The viewer is an immutable diagnostic surface: source
bytes are never edited, commands never replay or retry work, and every bit of
view state is discarded on close.

## Preview

The deterministic fixtures are complete plain-text frames produced by the
renderer, not illustrative mockups:

- [Simple](samples/simple.txt)
- [Verbose](samples/verbose.txt)
- [current and non-current search matches](samples/search-matches.txt)
- [recursively expanded provider JSON](samples/nested-expanded-json.txt)
- [truncated content](samples/truncated-content.txt)
- [truncated nested string leaves](samples/truncated-nested-leaves.txt)
- [malformed input](samples/malformed-input.txt)
- [tiny terminal](samples/tiny-terminal.txt)
- [plain / `NO_COLOR`](samples/plain-no-color.txt)

ANSI uses the same characters and geometry. Tests strip only SGR sequences and
require exact equality with the plain frame. The samples are compared byte for
byte with deterministic renderer cases in `tests/test_documented_samples.py`.

A search view looks like this in plain mode (the linked fixture is the exact
full frame):

```text
JSONL VIEWER • READ ONLY • SIMPLE
Session: session-01 • Conversation: conversation-01 • Agent: agent-01
@ 1 │ {
@ 1 │   "⟦r⟧equest_type": "request",
@ 1 │   "content": "Find the mismatch."
@ 1 │ }

* 2 │ {
* 2 │   "request_type": "response",
* 2 │   "content": "The field is missing."
* 2 │ }
1/4 occurrences • Search all text='r' • ⟦active⟧ • n/N
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
```

That fixture uses the default `Conversation` label and no subject. Supplying
`conversation_label="Debate"`, `conversation_id="FULL_ID"`, and
`conversation_subject="subject"` changes its header projection to
`Debate: FULL_ID — subject`; kind and subject are not concatenated into the ID.

A provider response stored as encoded JSON is displayed with its nested
structure instead of a wall of outer `\"` and `\\` escapes. This excerpt is
from the exact [nested sample](samples/nested-expanded-json.txt):

```text
> 1 │   "content": [expanded JSON string ×1] {
> 1 │     "action": "provider_response",
> 1 │     "response_text": [expanded JSON string ×1] {
> 1 │       "status": "provider_error",
> 1 │       "details": [
> 1 │         "correlation token mismatch",
> 1 │         {
> 1 │           "retryable": false
> 1 │         }
> 1 │       ]
> 1 │     }
> 1 │   }
Record 1/1 • source line 1 • JSON display 2 expanded, 0 skipped, 0 truncated
```

## Frame anatomy and immutable cues

Every normal frame has three areas:

1. **Header:** title, the literal `READ ONLY`, current Simple/Verbose mode, and
   only the session, scope label, exact conversation/scope ID, optional scope
   subject, and agent values supplied by `ViewerSpec`, before responsive
   clipping. The scope projection is `LABEL: FULL_ID — subject`; the em dash
   and subject are omitted when no subject is supplied.
2. **Body:** a stable source-record line gutter and pretty multiline JSON. The
   same source line number repeats on continuation rows so paging cannot detach
   content from its source identity.
3. **Footer:** transient status/search position followed by persistent command
   help. The normal footer retains at least `h help • q close` in the tiny
   tier. An active input draft replaces these two rows with its editor and
   submit/cancel/edit hint or validation error.

Plain gutter markers carry state without color:

| Marker | Meaning |
| --- | --- |
| `>` | selected/current source record |
| `@` | record containing the active keyword occurrence |
| `*` | another record containing keyword occurrences |
| space | visible record without one of those states |

For an object record, `Simple` contains only the configured date/time,
request-type, and content fields in that order. Its footer reports the hidden
top-level field count. `Verbose` places those three fields first and then
renders every other top-level field in source order. Ordinarily this is a JSON
projection, but a complete JSON object or array stored inside a string may be
shown as a visibly marked derived structure. The source record and its string
type remain unchanged. Non-object JSON records render their original value in
either mode, with the same derived-expansion and string-leaf preview rules.
Search initially selects the first keyword occurrence. Selecting a hit that
requires hidden object fields promotes to Verbose, including when `n`/`N`
reaches a later hit. Simple cannot be selected while the current hit requires
those hidden fields.

## Exact semantic roles

ANSI output uses only Select Graphic Rendition (SGR) sequences. It contains no
cursor movement, erase, raw-mode, alternate-screen, mouse, clipboard, title,
or hyperlink control. Those lifecycle operations belong exclusively to the
host.

| Semantic role | Plain signal | ANSI SGR | Exact usage |
| --- | --- | --- | --- |
| chrome | labels and separators | `1;36` bold cyan | the complete header line(s), Help body heading, and draft editor row |
| line-number gutter | repeated number and `│` | `2;90` dim bright-black | source line digits and gutter separator; the marker has its own role |
| JSON keys | quoted key | `34` blue | object field-name token; `:` and structural punctuation remain plain |
| strings | quoted JSON token | `32` green | content-safe string value, excluding a muted truncation marker |
| numbers | decimal token | `35` magenta | integers and finite floats |
| booleans / null | `true`, `false`, `null` | `33` yellow | JSON literal tokens |
| current record | `>` gutter marker | `1;36` bold cyan | selected record when it is not a search match |
| current match | `⟦keyword⟧`, `@`, and footer `i/N occurrences` | `1;30;43` bold black on yellow | active keyword span and current-match gutter marker; brackets use the current-record role |
| non-current match | `*` marker | `4;33` underlined yellow | other matching records' gutter marker only |
| warning/status | explanatory text | `1;33` bold yellow | non-error transient messages, including mode/goto/search status and hidden-field promotion |
| error | `! INPUT ERROR` / error wording | `1;31` bold red | input-diagnostic body and invalid host-event, goto, search, or draft-validation messages in the footer |
| muted | `…` and explanatory wording | `2;90` dim bright-black | `[expanded JSON string ×N]` and string-truncation cues; JSON-display/truncation and clipping footer facts; loading, empty, and unchanged-source safety text |
| footer/help | command words and ordinary status | `2;36` dim cyan | standard record status, search result when no message overrides it, persistent footer help, and draft editing hint |
| plain structure | JSON punctuation or help text | `0` reset/default | braces, brackets, commas, colons, whitespace, and Help command descriptions |

Every colored role has words, JSON punctuation, a gutter marker, or both. Color
is never the only distinction. `NO_COLOR`, `--no-color`, or a host returning
`False` from `color_enabled()` removes SGR only; semantic text remains exact.
Expanded object keys use blue, genuine string leaves use green, numbers use
magenta, booleans and `null` use yellow, and structural braces, brackets,
commas, and colons remain the terminal default. Expansion and truncation cues
use muted dim bright-black; a skipped-expansion cue is warning yellow. The
normal footer is dim cyan, while a record footer containing JSON display,
truncation, or clipping facts uses muted dim bright-black. An active search
footer remains dim cyan. Only the active occurrence's characters override
ordinary token colors; surrounding text retains its ordinary roles. The literal cues and footer counts carry
the same information in plain mode.

## Responsive geometry

The engine clamps a single frame to at most 240 columns and 100 rows so a
host-supplied geometry cannot cause unbounded rendering.

| Tier | Width | Header | Body/footer behavior |
| --- | --- | --- | --- |
| wide | 100–240 | one line with session, `LABEL: FULL_ID — subject`, and agent | full help, maximum JSON width |
| compact | 48–99 | mode line plus the same identifier projection | full help clipped by cells |
| narrow/tiny | 12–47 | `READ ONLY • MODE`; the second line starts `S:`, then the complete scope projection, then `A:`, with left-to-right cell clipping | reduced `h help • q close`; JSON remains guttered |

Height uses these exact allocations:

| Requested rows | Effective rows | Allocation |
| --- | --- | --- |
| 0–4 | 4 | header, then zero or one body row depending on header tier, then both footer rows |
| 5–99 | requested value | header, `rows - header rows - 2` body rows, then both footer rows |
| 100+ | 100 | the same allocation at the renderer's maximum height |

The two footer rows are protected; body rows shrink to zero before the normal
help/close cues or active draft rows disappear. Loading uses the same header
followed by its loading line and `h help • q close`, clipped only by the
effective height. Page Down advances
through the selected record's pretty rows and then crosses to the next source
record; Page Up reverses that behavior. JSON and chrome use Unicode cell width:
combining marks consume zero cells and East Asian wide/fullwidth characters
consume two. Clipped visual rows end with `…` and the footer reports `width
clipped`; clipping never changes the snapshot.

## States

- **Loading:** shown before bounded parsing as `Loading immutable JSONL
  snapshot…`; it performs no I/O beyond the already supplied bytes.
- **Empty:** says the immutable snapshot has no records; help and close remain.
- **Normal:** Simple or Verbose JSON with selection, goto, paging, and optional
  search state.
- **Error:** content-safe summary, source line where available, and
  an explicit statement that source bytes are unchanged. Raw malformed content
  is never echoed.
- **Help:** replaces the body, keeps the header/footer, and states that commands
  never edit, replay, or retry.
- **Input draft:** with `input_protocol="keys"`, `/` opens one
  `Search query:` draft; `g` opens `Go to source line:`. The engine owns
  draft text, insertion position, validation, submission, and cancellation.
  A printable `│` cursor appears in the first footer row; the second shows
  `Enter submit • Esc cancel • Ctrl+U/K/W edit` or a validation error.
  Below 32 columns labels shorten to `Query:` or `Line:`; long drafts
  clip around the logical cursor. Plain and ANSI frames have identical text.
- **Close:** once a valid `view_jsonl` call begins, it calls
  `host.close_view()` exactly once in `finally`, including after a host failure.
  It retains no frame, prompt draft, cursor, search, help, or navigation state.

Prompt cancellation is a viewer-engine transition shared by standalone and
embedded hosts using the physical-input protocol:

```text
committed viewer state
  → complete frame with transient search/goto draft and printable cursor
  → key<TAB>escape or key<TAB>unknown_escape
  → draft discarded + committed viewer state rendered at current geometry
```

The draft does not change the committed selection, mode, search, match index,
help, or status. Cancellation preserves those values, including the prior
`i/N`, without a host-maintained frame cache or editor. All prompt and viewer
state remains call-local and is neither persisted nor returned. No terminal
cursor control is embedded in a prompt frame.

## Keyboard and focus behavior

The package consumes host input without reading a terminal. `ViewerSpec.input_protocol` defaults to `"semantic"`. Version 0.3.0 retains
the three public exports and five host methods but changes the constructor
and search event; see the [migration guide](../README.md#migrating-to-030). Hosts selecting `"keys"` send `text<TAB>TEXT`,
`key<TAB>NAME`, or `line<TAB>LITERAL_COMMAND`; each payload is bounded to
8,192 characters independently of its envelope. Text accepts printable
Unicode, and line input may also contain tab separators. The host decodes
physical controls and bounded Escape sequences and preserves literal command
text. The engine owns bindings, actions, and line grammar. The exact key-name
vocabulary is listed in the [embedding contract](../README.md#embed-without-transferring-terminal-ownership).

The engine binds arrows or `j`/`k`, Page Up/Page Down or `b`/Space, `g`, `/`,
`n`, `N`, `m`, `h`/`?`, `c`, Escape, and `q`. In a draft, all printable
characters, including navigation and close letters, insert literal text.
Left/Right move by code point; Home/End or Ctrl-A/E move to the buffer ends;
Backspace/Delete remove the preceding/following code point. Ctrl-U deletes
before the cursor, Ctrl-K deletes after it, and Ctrl-W deletes preceding
whitespace and the preceding whitespace-delimited word. Up/Down and Page keys
leave the draft and committed viewer position unchanged. Goto drafts are bounded to 128 characters; query drafts to 1,024. Enter
validates and submits the draft; an empty query or nonpositive/noninteger
goto remains in its draft with a visible error.

Bare Escape and decoded `unknown_escape` cancel only an active draft. Ctrl-C
(`interrupt`) closes the view during a draft and otherwise applies semantic
`cancel`; physical EOF, `key<TAB>eof`, or `None` closes immediately. Hosts
perform bounded decoding and transport, without applying these actions or
opening another editor lifecycle. The standalone adapter uses `"keys"` for
both physical key input and ordinary lines.

The ordinary-line grammar accepts `g LINE`, `/ QUERY`,
`// QUERY`, `j`/`down`, `k`/`up`, `pgdn`, `pgup`, `n`, `N`, `m`,
`h`/`?`, `c`/`clear`, `q`/`quit`, and `esc`. The entire trimmed text
after the search prefix is the full-text query, including spaces. A separating
space after `/` or `//` is required before a query in this mode. Textual
`esc` applies semantic `cancel`. Semantic events are accepted in either protocol:

- `up` / `down`: select adjacent source records and reset within-record paging.
- `page_up` / `page_down`: page within a record, then cross record boundaries.
- `goto<TAB>LINE`: select an exact positive source-record line.
- `search<TAB>QUERY`: search all text with one nonempty query.
- `next_match` / `previous_match`: wrap through keyword occurrences and update `i/N`.
- `toggle_mode`: switch mode unless a selected hidden-field hit requires
  Verbose.
- `help`: open/close the transient help body.
- `cancel`: dismiss help, then search, then transient status; with nothing left
  to dismiss it closes.
- `close` or EOF: close immediately.

The body has a printable keyword focus cursor, but no editable JSON or
selection clipboard,
command execution, mouse link, or provider action. Search and goto accept only
transient viewer drafts; they never edit JSON or the source snapshot. The host
owns application focus and restores the surrounding view from `close_view()`;
the standalone adapter owns and restores its alternate-screen, hardware cursor
visibility, and terminal mode. The printable draft cursor provides a non-color
focus cue without transferring terminal ownership to the engine.

## Search semantics and result projection

Press `/`, enter a phrase at `Search query:`, and press Enter. Full-text
search covers all JSON root types, keys, scalar values, hidden fields, and
complete values before previews. Queries use Unicode `casefold` and literal
substrings, with no selectors, expressions, regular expressions, or wildcards.

| Search | Ordinary line | Semantic event |
| --- | --- | --- |
| phrase | `/ correlation token mismatch` | `search<TAB>correlation token mismatch` |
| single keyword | `/ provider_error` | `search<TAB>provider_error` |
| optional alias | `// two words` | `search<TAB>two words` |

Queries are nonempty printable text bounded to 1,024 characters. An absent
substring produces zero matches. More than 100,000 occurrences rejects the
new query transactionally with a refine-query message, retaining the committed
search and position. Invalid search events also preserve the committed search.
Version 0.3.0 removes `ViewerSpec.searchable_fields`, `--searchable-field`,
and all field/path search events; see the
[migration guide](../README.md#migrating-to-030).

Results follow source-record order, object insertion order, array order, and
left-to-right offsets within each key or value. Each nonoverlapping original
character span is one result. Casefold expansions map back to original code
points: both folded letters of `ß` identify one original character, so an
`s` query does not create duplicate or overlapping stops there.

Decoded children of complete strict JSON strings take precedence over raw
encodings. If a subtree has no direct hits, raw encoded text or compact
normalized JSON with sorted keys is searched as a fallback. This allows
structural matches while avoiding duplicate representations of direct hits.
When independent display decoding or projection cannot show an occurrence,
a `[raw search excerpt]`, `[normalized search excerpt]`, or
`[decoded search excerpt]` row supplies its focused text.

`n`/`N` wrap through occurrences, including multiple matches in one value,
and select the containing record. The active keyword is bracketed as
`⟦keyword⟧` in plain and ANSI output. Its record uses `@`; other matching
records use `*` without highlighting their whole JSON. The footer reports
`i/N occurrences`. Hidden-field selections promote to Verbose and prevent
Simple while that selected occurrence needs hidden fields.

A focused token uses up to 32 original code points of context on either side,
before safe JSON escaping. It can expose the selected match beyond ordinary
leaf previews. Vertical reveal brings the occurrence into the body when rows
are available. Manual paging can move away from it; the next `n`/`N`
reveals the newly selected occurrence. Resize recomputes geometry and focus
windows without changing search identity.

Horizontal windows below 24 cells prioritize `⟦keyword⟧` over context and
ellipsis. If the body after its gutter would have fewer than 12 cells, only
the active row omits that repeated gutter and uses the full frame width;
surrounding record rows retain their source gutters. Long keywords can still
be clipped to fit. At minimum height the protected footer may leave no body
row. These are printable focus cues, never hardware cursor movement.

`c` clears search. Cancellation follows the draft/main-view rules above;
search and focus state are discarded on close.

## Nested JSON, truncation, and control neutralization

Every displayed string leaf is considered for derived expansion before its
preview limit is applied. The renderer accepts only a complete strict JSON
value, allowing surrounding JSON whitespace, and expands it only if it resolves
to an object or array. It follows string-encoding layers recursively, so an
encoded provider `content` object can expose an encoded `response_text` object
inside it. Prose, JSON fragments, JSON scalars, malformed JSON, objects with
duplicate fields, and non-finite numbers stay genuine strings.

Search and display share these decoding bounds, applied independently per
source record. The last column describes the display fallback:

| Bound | Limit | Result when exceeded |
| --- | ---: | --- |
| string-encoding layers | 16 | original string plus `[JSON expansion skipped: encoding layer limit]` |
| projected display depth | 64 | original string plus `[JSON expansion skipped: display depth limit]` |
| derived value nodes | 200,000 | original string plus `[JSON expansion skipped: derived node limit]` |
| cumulative decoded UTF-8 bytes | 16,777,216 | original string plus `[JSON expansion skipped: decoded byte limit]` |

Each candidate expansion is transactional. Crossing a bound exposes no partial
container: the original string is rendered under the ordinary mode preview
limit, siblings already accepted remain expanded, and the footer increments
the skipped count. The display cue `[expanded JSON string ×N]` gives the
number of decoded string layers. These projections never mutate the source
bytes or parsed value types.

Search and rendering traverse independently with separate budgets. A selected
decoded hit receives a focused window or labeled excerpt even when ordinary
expansion or previews hide it. Geometry bounds still apply, and manual paging
can move away from the selected occurrence. A rejected decoding candidate
exposes no
partial children to search, while its original string remains searchable.

Simple displayed string leaves use a 4,096-byte complete UTF-8 prefix; Verbose
leaves use 65,536 bytes. Preview truncation is independent per leaf and never
removes a container, key, structural token, or child. A truncated leaf contains
a visible `[truncated RETAINED/TOTAL UTF-8 bytes]` marker. The footer aggregates
expanded, skipped, and truncated counts, plus retained/full UTF-8 bytes across
truncated leaves. A direct truncated content string also retains its dedicated
`content preview RETAINED/TOTAL UTF-8 bytes` fact. Terminal-width clipping and
vertical paging are later presentation steps with a separate `…`/`width
clipped` signal; they do not change expansion or preview accounting.

C0, C1, DEL, Unicode format/surrogate controls, line/paragraph separators, and
terminal escape bytes render as visible lower-case `\uXXXX` or `\uXXXXXXXX`
text. Newlines and tabs inside genuine JSON strings remain JSON escapes, as do
the quotes and backslashes required for valid JSON text. Derived expansion
removes only the extra escaping layer around a complete encoded object or
array; it does not unescape genuine text leaves. Every header part—including
the supplied scope label, ID, and subject—and every search query passes through
the same neutralization boundary before rendering.

## Privacy and data handling

Simple mode is not redaction or access control. Verbose mode can display every
field of a valid object record, subject to the documented preview and geometry
bounds. Full-text search covers all fields regardless of Simple-mode display
priority.
The embedding application must authorize the user, select the correct bounded
snapshot, and apply any retention or redaction policy before calling the
viewer.

The library does not open files, use the network, emit telemetry, persist view
state, or return viewed content. Its standalone adapter performs only the
explicit bounded path or standard-input read. Valid JSON values are displayed
after control neutralization; malformed input diagnostics expose a safe reason
and source location without echoing the malformed record.

## Nested diagnostic scenarios

1. **Provider exchange:** a JSONL exchange stores a complete JSON object in
   `content`, whose `response_text` string stores another complete object. Both
   strings expand recursively, their keys and values receive the ordinary JSON
   roles, and the two plain-text cues plus `2 expanded` footer fact identify the
   derived display.
2. **Long response leaf:** one expanded response contains a short summary and a
   long text leaf. The short value and all container structure remain intact;
   only the long leaf receives its retained/full marker, and the footer reports
   one truncated leaf with aggregate bytes. Width clipping or paging may still
   limit what fits in the current frame.
3. **Safety-bound skip:** a candidate would cross the layer, depth, node, or
   cumulative decoded-byte limit. The user sees the exact skip reason and the
   bounded original string. No partial keys or children from that candidate are
   displayed.
4. **Source and search immutability:** viewing an expanded response preserves
   source bytes and parsed string types. Full-text search inspects decoded
   children under bounded strict decoding rules, using original encoded text
   only when those children have no direct hit.

## End-to-end standalone scenario

1. The user snapshots a JSONL file and launches `jsonl-viewer` with explicit
   session, conversation/scope ID, and agent. Optional
   `--conversation-label` and `--conversation-subject` values project the scope
   as, for example, `Debate: FULL_ID — subject`; the label defaults to
   `Conversation`.
2. The standalone adapter requests at most one byte beyond the fixed snapshot
   bound so it can detect overflow before taking terminal ownership; the engine
   first shows Loading, then Simple.
3. The user presses `/`; the engine opens one query draft with a printable
   cursor. Submitting a phrase selects its first occurrence, shows
   `1/N occurrences`, and reveals `⟦keyword⟧`. After the draft closes,
   `n` and `N` wrap between individual occurrences.
4. During a search or goto draft, Escape discards the draft and
   renders the prior committed state without changing an existing result or
   position. Left/Right and Ctrl-U/K/W edit the draft; command letters insert
   literal text. Up/Down and Page keys are inert there. Bounded unsupported or
   incomplete Escape input is reported by the adapter and cancels the draft
   inside the engine.
5. `g` moves to a source line, `m` switches mode, Page Up/Page Down inspect a
   large record, main-view Escape cancels transient help/search/status, and `q`
   closes. The ordinary-line fallback accepts `/ QUERY`,
   `// QUERY`, and textual `esc` directly.
6. The adapter restores its terminal. The file bytes remain unchanged; frame,
   prompt-draft, and viewer state are discarded.

## End-to-end Story-hosted `/exchanges` scenario

This is a hosting use case. Story owns the surrounding application flow and
supplies generic inputs through the viewer's public contract:

1. In a Story session, the user enters `/exchanges` with no identifier
   arguments. Story opens a scope picker whose rows use the full label
   `Conversation FULL_ID — subject` or `Debate FULL_ID — subject`.
2. Canceling that picker returns to Story without opening the viewer. After a
   scope is chosen, Story opens its agent picker; canceling there returns to
   the prior Story surface without constructing a view.
3. Story acquires the selected agent's bounded immutable JSONL bytes and builds
   a generic `ViewerSpec`. It supplies the Story session ID, exact agent ID,
   full selected scope ID as `conversation_id`, exact kind as
   `conversation_label`, and subject as `conversation_subject`. The viewer
   header therefore reads
   `Conversation: FULL_ID — subject` or `Debate: FULL_ID — subject`;
   Story does not concatenate the kind or subject into the
   ID. The package does not interpret those generic values and shortens them
   only through content-safe responsive width clipping. Each supplied header
   string is limited to 512 characters.
4. An integration using 0.3.0 selects `input_protocol="keys"` and validates
   that capability before opening the terminal. Story's adapter passes the
   immutable bytes to `view_jsonl` and transports decoded physical text/keys
   or literal ordinary lines through `ViewerHost`. The engine owns all viewer
   bindings, command grammar, search/goto drafts, editing, and cancellation.
   Story remains sole owner of raw mode, signals, geometry, input, output,
   application focus, and cleanup. Integrations upgrading from older releases
   must update their dependency,
   constructor signature checks, and semantic search events as described in
   the migration guide.
5. A provider exchange whose `content` string is a complete JSON object is
   displayed as derived structure; a complete encoded `response_text` object
   inside it expands recursively. The literal expansion cues and footer counts
   distinguish that view from the unchanged source string.
6. The user confirms session/scope/agent context, presses `/`, and submits
   a full-text query. The viewer searches bounded decoded children, focuses
   each occurrence as `⟦keyword⟧`, reports `i/N occurrences`, and owns
   `n`/`N` traversal and hidden-hit Verbose promotion. The user can go to
   a source line, switch mode, inspect help, or use Escape to dismiss help,
   search, then status; an Escape with nothing transient closes.
7. Long nested string leaves receive independent retained/full byte markers;
   their parent objects, keys, siblings, and closing structure remain present
   and pageable. If a safety bound rejects an expansion, the user sees the
   reason and the bounded original string rather than a partial object.
8. On `q`, EOF, or the final Escape, `jsonl-viewer` calls `close_view`. Story
   restores its prior `/exchanges` scope/agent selection or shell frame.
9. Story owns the pickers, labels, snapshot location, and retention policy. No
   request, response, picker choice, or viewer state is modified or persisted
   by the viewer package.
