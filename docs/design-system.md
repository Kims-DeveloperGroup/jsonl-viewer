# JSONL Viewer Terminal Design System

This document is the repository-owned visual and interaction contract for
`jsonl-viewer` 0.1.0. The viewer is an immutable diagnostic surface: source
bytes are never edited, commands never replay or retry work, and every bit of
view state is discarded on close.

## Preview

The deterministic fixtures are complete plain-text frames produced by the
renderer, not illustrative mockups:

- [Simple](samples/simple.txt)
- [Verbose](samples/verbose.txt)
- [current and non-current search matches](samples/search-matches.txt)
- [truncated content](samples/truncated-content.txt)
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
@ 1 │   "request_type": "request",
@ 1 │   "content": "Find the mismatch."
@ 1 │ }

* 2 │ {
* 2 │   "request_type": "response",
* 2 │   "content": "The field is missing."
* 2 │ }
Search request_type='r' • 1/2 • @ current, * other
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
```

That fixture uses the default `Conversation` label and no subject. Supplying
`conversation_label="Debate"`, `conversation_id="FULL_ID"`, and
`conversation_subject="subject"` changes its header projection to
`Debate: FULL_ID — subject`; kind and subject are not concatenated into the ID.

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
   help. The footer always retains at least `h help • q close` in the tiny tier.

Plain gutter markers carry state without color:

| Marker | Meaning |
| --- | --- |
| `>` | selected/current source record |
| `@` | current search match (also selected when search initially lands) |
| `*` | another search match |
| space | visible record without one of those states |

For an object record, `Simple` is a valid JSON projection containing only the
configured date/time, request-type, and content fields in that order. Its
footer reports the hidden top-level field count. `Verbose` places those three
fields first and then renders every other top-level field in source order.
Non-object JSON records render as their original JSON value in either mode,
subject to the mode's string-preview bound. Searching a hidden object field
with at least one hit automatically selects the first hit and promotes to
Verbose; Simple cannot be selected again while that hidden-field match remains
active.

## Exact semantic roles

ANSI output uses only Select Graphic Rendition (SGR) sequences. It contains no
cursor movement, erase, raw-mode, alternate-screen, mouse, clipboard, title,
or hyperlink control. Those lifecycle operations belong exclusively to the
host.

| Semantic role | Plain signal | ANSI SGR | Exact usage |
| --- | --- | --- | --- |
| chrome | labels and separators | `1;36` bold cyan | the complete header line(s) and the Help body heading |
| line-number gutter | repeated number and `│` | `2;90` dim bright-black | source line digits and gutter separator; the marker has its own role |
| JSON keys | quoted key | `34` blue | object field-name token; `:` and structural punctuation remain plain |
| strings | quoted JSON token | `32` green | content-safe string value, excluding a muted truncation marker |
| numbers | decimal token | `35` magenta | integers and finite floats |
| booleans / null | `true`, `false`, `null` | `33` yellow | JSON literal tokens |
| current record | `>` gutter marker | `1;36` bold cyan | selected record when it is not a search match |
| current match | `@` plus footer `i/N` | `1;30;43` bold black on yellow | current-match marker and every non-whitespace token on the searched top-level field's rendered rows |
| non-current match | `*` marker | `4;33` underlined yellow | other-match marker and every non-whitespace token on its searched-field rows |
| warning/status | explanatory text | `1;33` bold yellow | non-error transient messages, including mode/goto/search status and hidden-field promotion |
| error | `! INPUT ERROR` / error wording | `1;31` bold red | input-diagnostic body and invalid host-event, goto, or search messages in the footer |
| muted | `…` and explanatory wording | `2;90` dim bright-black | string truncation marker; a truncation/clipping footer; loading, empty, and unchanged-source safety text |
| footer/help | command words and ordinary status | `2;36` dim cyan | standard record status, search result when no message overrides it, and persistent footer help |
| plain structure | JSON punctuation or help text | `0` reset/default | braces, brackets, commas, colons, whitespace, and Help command descriptions |

Every colored role has words, JSON punctuation, a gutter marker, or both. Color
is never the only distinction. `NO_COLOR`, `--no-color`, or a host returning
`False` from `color_enabled()` removes SGR only; semantic text remains exact.

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

The footer is protected; body rows shrink to zero before help/close cues
disappear. Loading uses the same header followed by its loading line and
`h help • q close`, clipped only by the effective height. Page Down advances
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
- **Close:** once a valid `view_jsonl` call begins, it calls
  `host.close_view()` exactly once in `finally`, including after a host failure.
  It retains no frame, cursor, search, help, or navigation state.

## Keyboard and focus behavior

The package consumes host events, not terminal keys. The standalone adapter
maps arrows or `j`/`k`, Page Up/Page Down or `b`/Space, `g`, `/`, `n`, `N`,
`m`, `h`/`?`, Escape, and `q`. An embedding host may use different bindings
while preserving the closed event vocabulary.

- `up` / `down`: select adjacent source records and reset within-record paging.
- `page_up` / `page_down`: page within a record, then cross record boundaries.
- `goto<TAB>LINE`: select an exact positive source-record line.
- `search<TAB>FIELD<TAB>QUERY`: search one exact enumerated top-level field.
- `next_match` / `previous_match`: wrap through hits and update `i/N`.
- `toggle_mode`: switch mode unless a selected hidden-field hit requires
  Verbose.
- `help`: open/close the transient help body.
- `cancel`: dismiss help, then search, then transient status; with nothing left
  to dismiss it closes.
- `close` or EOF: close immediately.

There is no editable focus, cursor inside JSON, selection clipboard, command
execution, mouse link, or provider action. The host owns application focus and
restores the surrounding view from `close_view()`; the standalone adapter owns
and restores its alternate-screen, cursor visibility, and terminal mode.

## Truncation and control neutralization

Simple projected strings use a 4,096-byte complete UTF-8 prefix; Verbose
strings use 65,536 bytes. A truncated token contains a visible
`[truncated RETAINED/TOTAL UTF-8 bytes]` marker and the footer repeats content
preview accounting. Terminal-width clipping has a separate `…`/`width clipped`
signal. Neither means the source was changed.

C0, C1, DEL, Unicode format/surrogate controls, line/paragraph separators, and
terminal escape bytes render as visible lower-case `\uXXXX` or `\uXXXXXXXX`
text. Newlines and tabs inside JSON strings remain JSON escapes. Every header
part—including the supplied scope label, ID, and subject—and every search query
passes through the same neutralization boundary before rendering.

## Privacy and data handling

Simple mode is not redaction or access control. Verbose mode can display every
field of a valid object record, subject to the documented preview and geometry
bounds, and searchable-field enumeration limits search rather than visibility.
The embedding application must authorize the user, select the correct bounded
snapshot, and apply any retention or redaction policy before calling the
viewer.

The library does not open files, use the network, emit telemetry, persist view
state, or return viewed content. Its standalone adapter performs only the
explicit bounded path or standard-input read. Valid JSON values are displayed
after control neutralization; malformed input diagnostics expose a safe reason
and source location without echoing the malformed record.

## End-to-end standalone scenario

1. The user snapshots a JSONL file and launches `jsonl-viewer` with explicit
   session, conversation/scope ID, agent, and allowed search fields. Optional
   `--conversation-label` and `--conversation-subject` values project the scope
   as, for example, `Debate: FULL_ID — subject`; the label defaults to
   `Conversation`.
2. The standalone adapter requests at most one byte beyond the fixed snapshot
   bound so it can detect overflow before taking terminal ownership; the engine
   first shows Loading, then Simple.
3. The user presses `/`, enters an enumerated field and query, and sees
   `1/N`; `n` and `N` wrap through results.
4. `g` moves to a source line, `m` switches mode, Page Up/Page Down inspect a
   large record, Escape cancels transient help/search/status, and `q` closes.
5. The adapter restores its terminal. The file bytes and viewer state are
   unchanged.

## End-to-end Story-hosted `/exchanges` scenario

This is an integration scenario, not behavior or schema built into this repo:

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
   header therefore reads `Conversation: FULL_ID — subject` or `Debate:
   FULL_ID — subject`; Story does not concatenate the kind or subject into the
   ID. The package does not interpret those generic values and shortens them
   only through content-safe responsive width clipping. Each supplied header
   string is limited to 512 characters.
4. Story's adapter maps its existing raw terminal driver events to the closed
   `ViewerHost` event strings and passes the immutable bytes to `view_jsonl`.
   Story remains sole owner of raw mode, signals, geometry, input, output,
   application focus, and cleanup.
5. The user confirms session/scope/agent context in the header, searches only
   Story-enumerated fields, sees the transient `i/N` result in the footer, and
   uses `n`/`N` to wrap through hits. The user can go to a physical JSONL source
   line, switch Simple/Verbose, inspect help, and use Escape to dismiss help,
   then search, then status; an Escape with nothing transient closes.
6. On `q`, EOF, or the final Escape, `jsonl-viewer` calls `close_view`. Story
   restores its prior `/exchanges` scope/agent selection or shell frame.
7. Story owns the pickers, labels, snapshot location, and retention policy. No
   request, response, picker choice, or viewer state is modified or persisted
   by the viewer package.
