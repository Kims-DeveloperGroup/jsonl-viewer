# jsonl-viewer

`jsonl-viewer` is a read-only, transient, structured JSONL viewer for Python
terminals. It parses one bounded immutable byte snapshot, renders colored
multiline JSON with source-record line gutters, and discards navigation,
search, help, and mode state when the view closes.

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
- literal, case-insensitive full-text or arbitrary field/path search, including
  decoded nested JSON, with `n`/`N`, current/total results, and automatic
  Verbose promotion for a selected hidden-field hit;
- deterministic semantic ANSI or exactly equivalent plain output;
- viewer-owned key bindings, ordinary-line command grammar, and bounded search
  and go-to-line drafts through an opt-in physical-input protocol;
- Unicode-aware cell clipping and visible neutralization of embedded terminal
  controls, bidi controls, and other format controls; and
- a standalone terminal owner plus an injected host boundary for applications
  that already own raw mode, signals, geometry, input, output, and cleanup.

There is no writer API, persistence, live tail, network access, replay, retry,
or arbitrary search-expression language.

## Preview

This excerpt shows the plain semantic view. ANSI-capable terminals add color
without changing the text, markers, or layout:

```text
JSONL VIEWER • READ ONLY • SIMPLE
Session: session-01 • Debate: d20260829T090000_abcd1234 — Provider diagnostics • Agent: agent-01
@ 1 │ {
@ 1 │   "timestamp": "2026-08-29T09:00:00Z",
@ 1 │   "request_type": "request",
@ 1 │   "content": "Find the mismatch."
@ 1 │ }

* 2 │ {
* 2 │   "timestamp": "2026-08-29T09:00:01Z",
* 2 │   "request_type": "response",
* 2 │   "content": "The field is missing."
* 2 │ }
Search request_type='r' • 1/2 • @ current, * other
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
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
state. Nine [deterministic full-frame samples](docs/samples/README.md) cover
Simple, Verbose, search, nested expansion, leaf truncation, malformed input,
tiny terminals, and plain / `NO_COLOR` output.

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

`--searchable-field FIELD` remains an optional, repeatable compatibility preset.
Presets do not filter search; omitting them enables the same full-text and
arbitrary-field search.

`NO_COLOR` or `--no-color` disables ANSI color without changing any semantic
text or marker. A non-TTY uses an ordinary-line command fallback. Use `-` as
the path to snapshot standard input; because that consumes stdin, the view is
then rendered once and closes on EOF.

Standalone keys are `↑`/`↓` or `j`/`k`, Page Up/Page Down or `b`/Space, `g`,
`/`, `n`, `N`, `m`, `h`/`?`, `c` to clear search, Escape, and `q`. In
ordinary-line mode, use `g LINE` to navigate and the
[search commands below](#search-in-ordinary-line-mode) to search.

On a supported interactive terminal, `/` opens `Search field:` and then
`Search query:`; follow the [search walkthrough](#search-text-and-nested-values)
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

You do not need to enumerate fields: all top-level fields and nested paths
are searchable, including fields hidden in Simple mode. Search works without
any `--searchable-field` presets.

The examples use this record, formatted across lines for illustration. In a
JSONL file, each complete record must occupy a single line.

```json
{
  "content": "Request failed",
  "payload": {
    "items": [
      {"status": "provider_error", "details": "Correlation token mismatch"},
      {"status": "ok", "details": "Ready"}
    ]
  }
}
```

### Find text anywhere

1. Press `/` to open `Search field:`.
2. Leave the field blank and press Enter.
3. At `Search query:`, type `correlation token mismatch` and press Enter.

This finds the example record through its nested `details` value. Full-text
search covers **keys and values**, including nested fields and complete values
hidden by display previews. It works with any JSON root type. Queries match
literal substrings and ignore case, so `PROVIDER_ERROR` and `error` both find
`provider_error`; `status` also finds the key named `status`.

### Search a particular nested value

1. Press `/`.
2. At `Search field:`, type `payload.items[0].status` and press Enter.
3. At `Search query:`, type `provider_error` and press Enter.

The dots follow object keys, and `[0]` selects the first array item; `[1]`
selects the second. This path searches only the first item's `status` value.
To search everything inside the array, enter `payload.items` instead. A path
to an object or array searches its subtree, including its keys and values.

Field and key names are case-sensitive: `payload` and `Payload` are different
keys. Query text remains case-insensitive. Try these searches on the same
record:

| Search field | Search query | Expected result |
| --- | --- | --- |
| Leave blank | `correlation token mismatch` | Matches the first item's `details`. |
| Leave blank | `status` | Matches the nested key name. |
| `payload.items[0].status` | `ERROR` | Matches the substring in `provider_error`. |
| `payload.items` | `ready` | Matches `details` in the second item. |
| `payload.items[0].status` | `ready` | No match: the selected value is `provider_error`. |
| `payload.items[1].status` | `ok` | Matches the second item's `status`. |

### Move through results and clear a search

Each matching source record counts once, even if several keys or values
match. The footer shows your current result and the total. Press `n` for the
next matching record or `N` for the previous one; both wrap through records
in source order. Press `c` to clear the search.

Selecting a hit that needs hidden fields automatically switches to Verbose,
including when you use `n` or `N`. Simple mode remains unavailable while the
selected hit needs those fields. A match can still be outside a string preview,
the current page, or the terminal width; record markers and result counts
identify the matching record. Escape follows the cancellation rules above.

### Search in ordinary-line mode

If your terminal uses the ordinary-line fallback, type a complete command and
press Enter:

| Command form | What it searches |
| --- | --- |
| `// QUERY` | All text; the query can contain multiple words. |
| `/ TOKEN` | All text for a single-token query. |
| `/ FIELD QUERY` | The specified field or path; the query can contain multiple words. |

These commands use the same example record:

```text
// correlation token mismatch
/ provider_error
/ payload.items[0].status provider_error
/ /payload/items/0/status provider_error
/ payload.items[0].details correlation token mismatch
```

Each command finds the record. The two `status` commands select the same value:
`/payload/items/0/status` is the JSON Pointer form of `payload.items[0].status`.
The command's first `/` is separate from the pointer's leading `/`.

Use `//` for a full-text phrase: `/ two words` searches field `two` for `words`,
while `// two words` searches all text for the complete phrase.

### Advanced path and matching rules

- **Literal top-level keys take precedence.** For each object record, an exact
  nonempty top-level key wins over path interpretation, even if it contains
  dots, brackets, or leading slashes. If `"payload.items[0].status"` is itself
  a top-level key, that key is searched first. Without that exact key, the
  selector follows the nested path. A blank field always means full text.
- **JSON Pointer supports unusual keys.** `~0` means `~`, `~1` means `/`, and
  empty segments are preserved. `/a~1b/~0key/` selects the empty key inside
  `~key` inside `a/b`. `/` selects an empty top-level key unless a literal `/`
  top-level key exists, which takes precedence.
- **Root arrays work too.** Use `[0].status` or `/0/status` to reach the first
  item's `status` in a root array. Array indexes are zero-based, nonnegative
  integers with no leading zeros except `0` itself.
- **Matching is literal.** Queries use Unicode `casefold` and substring
  matching. There are no regular expressions, wildcards, or expression
  evaluation. Queries spanning object/array structure use compact normalized
  JSON with sorted object keys, independent of source whitespace or key order.
- **JSON stored inside strings can be searched.** Full-text search and deeper
  paths can decode complete strict JSON objects or arrays inside strings,
  including repeated encoding, under the
  [shared bounds](#input-and-safety-contract). Selecting a string itself keeps
  literal substring matching against its original contents. For example, if
  `payload` contains encoded JSON, `payload.items[0].status` can traverse into
  it, while selecting `payload` searches the original string.
- **Limits and corrections.** Across input protocols, fields/paths allow up to
  128 characters and nonempty queries up to 1,024. A valid search with a missing
  field/path or an absent substring returns no matches. Empty queries are
  rejected. Malformed paths produce a correctable error, unless they match an
  exact top-level key under the precedence rule above.

## Embed without transferring terminal ownership

```python
from jsonl_viewer import ViewerHost, ViewerSpec, view_jsonl

spec = ViewerSpec(
    session_id="session-01",
    conversation_id="debate-full-id",
    agent_id="agent-01",
    searchable_fields=(),
    conversation_label="Debate",
    conversation_subject="Provider response diagnostics",
    input_protocol="keys",
)

# `host` implements ViewerHost using the application's existing terminal
# driver. The viewer never enters raw mode, clears a screen, handles signals,
# reads a file, or closes that terminal.
view_jsonl(snapshot_bytes, spec, host)
```

`searchable_fields` remains a required constructor parameter with the same
signature. Pass `()` for no presets, or retain existing bounded preset values
as compatibility metadata; they never restrict searchable fields or text.
The `primary_fields` property, derived from `date_time_field`,
`request_type_field`, and `content_field`, controls Simple-mode display priority
and does not constrain search.

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
`unknown`. Search-field and goto drafts are bounded to 128 characters; search
query drafts are bounded to 1,024. Drafts and their logical cursors arrive in
ordinary complete frames, so the host needs no prompt buffer or second input
lifecycle. The standalone adapter uses this protocol for both terminal keys
and ordinary lines.

For existing integrations, `ViewerSpec.input_protocol` is appended with the
default `"semantic"`. Upgrading from 0.1.1 preserves the three public exports,
existing constructor arguments, all five host methods, and this closed
semantic event vocabulary:

```text
up | down | page_up | page_down | next_match | previous_match
toggle_mode | help | cancel | clear_search | close
goto<TAB>POSITIVE_SOURCE_LINE
search<TAB>FIELD_OR_PATH<TAB>NONEMPTY_QUERY
search<TAB><TAB>NONEMPTY_QUERY
```

The second search form leaves the field empty for full-text search. Existing
field/query events retain literal matching when the selected value is a string.

`None` means EOF/close in either protocol. Existing semantic events are also
accepted in `"keys"` mode. Unknown semantic events produce a transient help
hint. A legacy semantic host retains its input-to-event mapping; a host opting
into `"keys"` supplies physical inputs and lets the viewer handle actions.
Hosts that validate exact constructor signatures must accept the appended
`input_protocol` field before opening a view. Terminal modes, signals,
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
decoded hit can remain behind a display expansion skip, a string preview,
width clipping, or paging; record markers and result counts still identify
the hit. A displayed leaf that is still a genuine string uses normal JSON
escaping: embedded quotes and backslashes remain escaped, while unsafe control
and format characters remain visible as `\uXXXX` text.

Blank records, duplicate object fields, non-finite numbers, malformed JSON,
invalid UTF-8, and exceeded bounds render content-safe diagnostics. Source
values are untrusted: control and format characters are rendered as visible
`\uXXXX` text. Color is never the only signal.

Simple mode is an orientation view, not a privacy boundary. Verbose mode can
show every field in a valid record; preset fields do not restrict search or
visibility. The embedding application is responsible for authorizing,
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
