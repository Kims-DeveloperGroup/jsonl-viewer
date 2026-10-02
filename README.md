# jsonl-viewer

`jsonl-viewer` is a read-only, transient, structured JSONL viewer for Python
terminals. It parses one bounded immutable byte snapshot, renders colored
multiline JSON with source-record line gutters, and discards navigation,
cursor, fold, search, help, and mode state when the view closes.

The package is Python 3.11+, MIT licensed, and has no runtime dependencies. It
has no knowledge of Story, agent runtimes, storage layouts, provider schemas,
or terminal-driver implementations.

## Capabilities

- a three-name public API: `ViewerSpec`, `ViewerHost`, and `view_jsonl`;
- Simple and Verbose views, with configurable date/time, request-type, and
  content fields ordered first;
- recursive, bounded expansion of string values that contain complete strict
  JSON objects or arrays, including multiply encoded provider payloads;
- per-string-leaf UTF-8 previews (4 KiB in Simple and 64 KiB in Verbose), with
  explicit retained/full byte counts and aggregate display facts;
- strict UTF-8 JSONL parsing with bounded, content-safe malformed-input views;
- stable source-record line gutters, record navigation, paging, and exact
  go-to-line;
- literal, case-insensitive full-text search, including decoded nested JSON,
  with `n`/`N` moving between individual keyword occurrences, visible keyword
  focus, current/total counts, and hidden-hit Verbose promotion;
- character navigation and structural folding with readable ANSI focus or a plain caret;
- viewer-owned key bindings, ordinary-line command grammar, and bounded search
  and go-to-line drafts through an opt-in physical-input protocol;
- Unicode-aware cell clipping and visible neutralization of embedded terminal
  controls, bidi controls, and other format controls; and
- a standalone terminal owner plus an injected host boundary for applications
  that already own raw mode, signals, geometry, input, output, and cleanup.

There is no writer API, persistence, live tail, network access, replay, retry,
or arbitrary search-expression language.

## Preview

This excerpt shows the plain view. ANSI uses inverse-video character focus;
plain output inserts an aligned caret row below the focused JSON row:

```text
JSONL VIEWER • READ ONLY • SIMPLE
Session: session-01 • Debate: d20260829T090000_abcd1234 — Provider diagnostics • Agent: agent-01
@ 1 │ {
@ 1 │   "timestamp": "2026-08-29T09:00:00Z",
@ 1 │   "⟦r⟧equest_type": "request",
          ^
@ 1 │   "content": "Find the mismatch."
@ 1 │ }

* 2 │ {
* 2 │   "timestamp": "2026-08-29T09:00:01Z",
* 2 │   "request_type": "response",
* 2 │   "content": "The field is missing."
* 2 │ }
1/4 occurrences • Search all text='r' • ⟦active⟧ • n/N
h/j/k/l cursor • Enter fold • ↑/↓ records • PgUp/PgDn • / search • n/N • m mode • ? help • q close
```

An encoded provider response is expanded as a derived, read-only display. The
cue is visible without color, and genuine string leaves retain the JSON
escaping required for quotes, backslashes, and control characters:

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

The [design system](docs/design-system.md) defines every visual role and user
state. Eleven [deterministic full-frame samples](docs/samples/README.md) cover
Simple, Verbose, search, nested expansion, leaf truncation, malformed input,
cursor folding, search through folds, tiny terminals, and plain / `NO_COLOR` output.

## Install and run

```bash
python -m pip install jsonl-viewer
jsonl-viewer exchanges.jsonl \
  --session session-01 \
  --conversation debate-full-id \
  --conversation-label Debate \
  --conversation-subject "Provider response diagnostics" \
  --agent agent-01
```

`NO_COLOR` or `--no-color` disables ANSI styling and uses an aligned caret row
under the focused JSON character, leaving one fewer data row when space allows.
A non-TTY uses an ordinary-line command fallback. Use `-` as
the path to snapshot standard input; because that consumes stdin, the view is
then rendered once and closes on EOF.

Standalone keys are `h/j/k/l` for the character cursor, Up/Down for records,
Page Up/Page Down or `b`/Space for paging, Enter to fold, `g`, `/`, `n`, `N`,
`m`, `?` for help, `c` to clear search, Escape, and `q`. In
ordinary-line mode, use `g LINE` to navigate and the
[search commands below](#search-in-ordinary-line-mode) to search.

On a supported interactive terminal, `/` opens one `Search query:` draft;
follow the [search walkthrough](#search-text-and-nested-values)
below. `g` opens `Go to source line:`. The viewer engine owns these drafts
and shows a printable `│` insertion cursor in
the frame footer. Enter submits the current stage. Left/Right, Home/End or
Ctrl-A/E, Backspace, and Delete edit the draft. Ctrl-U deletes before the
cursor, Ctrl-K deletes after it, and Ctrl-W deletes the preceding word and
intervening whitespace. Letters such as `j`, `k`, `n`, `q`, and `g` are literal
text while a draft is open;
Up/Down and Page keys leave the draft and viewer position unchanged.

Escape discards only the unfinished search or goto draft and renders the prior
committed viewer state, preserving any active search and its current `i/N`
position. Unsupported or incomplete Escape sequences are bounded by the host
decoder and reported as `unknown_escape`, which also cancels a draft. Ctrl-C
closes the view during a prompt; in the main view it applies Escape's existing
cancel behavior. EOF always closes. In the main view, Escape dismisses help,
then search, then transient status, and otherwise closes. Ordinary-line
`g LINE` and textual `esc` retain their existing grammar.

## Search text and nested values

Press `/`, type a phrase at `Search query:`, and press Enter. For example,
`correlation token mismatch` finds that text anywhere in a record, including
nested JSON stored inside strings. Search includes keys, scalar values, every
JSON root type, hidden fields, and complete values beyond display previews.
Queries are literal, case-insensitive substrings; there are no regular
expressions, wildcards, or field selectors.

### Move through keyword occurrences

The first occurrence is focused as `⟦keyword⟧`. Press `n` for the next
occurrence or `N` for the previous one, wrapping at either end. Multiple
matches within one value or record are separate stops. The footer reports
`i/N occurrences`; `@` marks the active occurrence's record and `*` marks
other matching records. Only the active keyword receives the search highlight.

The viewer brings the selected occurrence into view vertically and shows a
bounded text window around it, even beyond a long value's normal preview.
Hidden-field hits promote the view to Verbose. Manual paging remains available;
`n` or `N` brings the next selected occurrence back into view. Resizing
recomputes the window at the current geometry. At very narrow widths, the
active keyword takes priority over context and its repeated gutter. Long
keywords can still be clipped to the available cells. Press `c` to clear
search; Escape follows the cancellation rules above.

### Search in ordinary-line mode

Type `/ correlation token mismatch` and press Enter. A separating space after
`/` is required in ordinary-line mode; the entire phrase is the query.
`// correlation token mismatch` is also accepted as a full-text alias, with
the same separator requirement. Leading and trailing command whitespace is
trimmed.

### Matching and bounds

Occurrences follow source-record order, object insertion order and array
order, then left-to-right offsets within each key or value. Unicode
`casefold` matching maps back to original characters: searching `s` in
`ß` produces one focused occurrence, not two overlapping highlights.
Overlapping original-character spans are suppressed.

Complete strict JSON objects/arrays encoded inside strings are decoded under
the [shared bounds](#input-and-safety-contract). Decoded children take
precedence over their raw representation. When a subtree has no direct hits,
search can fall back to its original encoded string or compact normalized JSON
with sorted keys, allowing structural phrases without duplicate representation
hits. If a match cannot be shown in the normal projection, a labeled
`[raw search excerpt]`, `[normalized search excerpt]`, or
`[decoded search excerpt]` provides its focused text.

Queries must be nonempty, contain supported printable text, and have at most
1,024 characters. An absent substring produces no matches. More than 100,000
occurrences rejects the new search with a request to refine the query; the
previous committed search and position remain intact. Invalid input also
preserves the committed search.

## Embed without transferring terminal ownership

```python
from jsonl_viewer import ViewerHost, ViewerSpec, view_jsonl

spec = ViewerSpec(
    session_id="session-01",
    conversation_id="debate-full-id",
    agent_id="agent-01",
    conversation_label="Debate",
    conversation_subject="Provider response diagnostics",
    input_protocol="keys",
)

# `host` implements ViewerHost using the application's existing terminal
# driver. The viewer never enters raw mode, clears a screen, handles signals,
# reads a file, or closes that terminal.
view_jsonl(snapshot_bytes, spec, host)
```

The `primary_fields` property, derived from `date_time_field`,
`request_type_field`, and `content_field`, controls Simple-mode display
priority and does not constrain search.

### Migrating to 0.4.x

Main-view `h` now moves left, and `j`/`k` move down/up within visible JSON.
Use `?` for help and the Up/Down arrows to select records. `l` moves right;
Enter folds the innermost nonempty object or array containing the cursor.
Ordinary-line input accepts `h`, `j`, `k`, `l`, `fold`, and `?`/`help`.
Prompt editing retains its existing keys and treats these letters as text.
Semantic hosts can send `cursor_left`, `cursor_right`, `cursor_up`,
`cursor_down`, and `toggle_fold`; existing `up`, `down`, and `help` events
retain their meanings. The three public exports and five host methods remain
unchanged.

From 0.4.1, `l` at a row's end moves to the next visible JSON row's first
character; `h` at a row's start moves to the previous row's last character. Both
stop at the viewport boundaries without scrolling. Gutters, display annotations,
caret rows, and record separators are skipped.

The cursor starts at the first JSON character of the top visible data row.
Successful record, page, go-to-line, and mode changes reset it there. Vertical
movement preserves its preferred display column and resizing clamps it to
visible content. Gutters and display annotations are skipped. ANSI output uses
a readable inverse-video cell and bolds visible matching container delimiters;
plain/NO_COLOR output reserves a caret row underneath the focused row.
The layouts therefore intentionally differ by that row.

Folded objects and arrays appear as `{…}` and `[…]`, preserving their keys,
commas, and encoded-JSON expansion cues. Fold state belongs to a record and
structural path for this viewer session; reopening a parent restores nested
folds. Enter leaves the cursor on the toggled opening delimiter. Empty
containers and scalar roots do nothing. Braces inside strings are text.
Search and `n`/`N` unfold the selected hit's ancestors and focus its first
character without changing occurrence counts. Manually folding an active hit
stays effective until the next search navigation.

### Migrating to 0.3.0

Version 0.3.0 deliberately removes field-scoped search. Remove
`searchable_fields` from `ViewerSpec` calls and `--searchable-field` from
CLI commands. When migrating positional constructor calls, remove the former
fourth argument; prefer keywords for optional display fields. Send
`search<TAB>QUERY` instead of the former three-part field/query event,
including the former blank-field form. Three-part events are rejected.
Ordinary `/ content text` now searches the complete phrase `content text`;
it does not select `content`. Remove the field portion to search for `text`
anywhere. Hosts validating the exact constructor signature must update that
check. The three public exports, five host methods, and terminal ownership
remain unchanged.

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

`input_protocol="keys"` lets the viewer own all key bindings, command grammar,
prompt editing, and cancellation. The host decodes physical input into these
envelopes; `<TAB>` denotes one literal tab:

```text
text<TAB>TEXT
key<TAB>PHYSICAL_KEY_NAME
line<TAB>LITERAL_COMMAND
```

Each payload is limited to 8,192 characters, excluding its envelope. Text
contains printable Unicode; literal line input may also contain tab separators.
The host preserves command text and leaves its interpretation to the viewer.
Physical key names are `enter`, `escape`, `unknown_escape`, `backspace`,
`delete`, `left`, `right`, `home`, `end`, `up`, `down`, `page_up`, `page_down`,
`ctrl_a`, `ctrl_e`, `ctrl_u`, `ctrl_k`, `ctrl_w`, `interrupt`, `eof`, and
`unknown`. Goto drafts are bounded to 128 characters; search query drafts to
1,024. Drafts and their logical cursors arrive in
ordinary complete frames, so the host needs no prompt buffer or second input
lifecycle. The standalone adapter uses this protocol for both terminal keys
and ordinary lines.

`ViewerSpec.input_protocol` defaults to `"semantic"`. Its closed event
vocabulary is:

```text
up | down | page_up | page_down | next_match | previous_match
toggle_mode | help | cancel | clear_search | close
cursor_left | cursor_right | cursor_up | cursor_down | toggle_fold
goto<TAB>POSITIVE_SOURCE_LINE
search<TAB>NONEMPTY_QUERY
```

`None` means EOF/close in either protocol. These semantic events are also
accepted in `"keys"` mode. Unknown semantic events produce a transient help
hint. A semantic host owns its input-to-event mapping; a host opting
into `"keys"` supplies physical inputs and lets the viewer handle actions.
Terminal modes, signals,
geometry, presentation, application focus, and restoration remain host-owned.

## Input and safety contract

`view_jsonl` accepts exact immutable `bytes`. It never accepts a writable
stream or path and never mutates the object. The standalone adapter opens a
path only for a bounded binary read.

- snapshot bound: 16,777,216 bytes;
- record bound: 2,097,152 UTF-8 bytes;
- record-count bound: 10,000;
- nesting bound: 64 levels;
- value-node bound: 200,000 per record;
- nested JSON decoding, independently for search and display: at most 16
  encoding layers, 64 projected levels, 200,000 derived nodes, and 16,777,216
  cumulative decoded UTF-8 bytes per record;
- Simple string-leaf preview: at most 4,096 UTF-8 bytes at a complete code
  point; and
- Verbose string-leaf preview: at most 65,536 UTF-8 bytes.

Before applying a preview, the renderer recursively expands a displayed
string only when its complete strict JSON value (apart from surrounding JSON
whitespace) resolves to an object or array. Prose, fragments, scalar JSON,
malformed JSON, duplicate object fields, and non-finite numbers remain genuine
JSON strings. Expansion never truncates a container, key, structural token, or
child. If an expansion would cross a safety bound, the renderer shows a
`[JSON expansion skipped: REASON]` cue and the bounded original string instead;
it never exposes a partial container.

Expansion is a derived presentation, so a displayed object is not always a
type-preserving JSON projection of the source record. The immutable source
value remains a string. Search and rendering traverse independently under the
same strict decoding rules and bounds, with separate per-record budgets. A
selected occurrence receives a bounded focused window or labeled excerpt,
including when display expansion skips or previews would hide it. Geometry
still limits the visible window; manual paging can move away from the focus.
A displayed leaf that is still a genuine string uses normal JSON
escaping: embedded quotes and backslashes remain escaped, while unsafe control
and format characters remain visible as `\uXXXX` text.

Blank records, duplicate object fields, non-finite numbers, malformed JSON,
invalid UTF-8, and exceeded bounds render content-safe diagnostics. Source
values are untrusted: control and format characters are rendered as visible
`\uXXXX` text. Color is never the only signal.

Simple mode is an orientation view, not a privacy boundary. Verbose mode can
show every field in a valid record; search covers all text. The embedding
application is responsible for authorizing,
selecting, retaining, and redacting snapshot data. The library performs no
filesystem or network I/O, telemetry, or persistence; viewer prompt drafts and
frames exist only while the view is open. The standalone adapter performs
only its explicit bounded file
or standard-input read.

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
