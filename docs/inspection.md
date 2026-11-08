# Generic inspection

Inspection adds a record list, optional groups and sections, typed filters, and
named search ranges to one immutable JSONL snapshot. It does not define an
application lifecycle. A log level, backend name, workflow state, or section
label is ordinary caller-supplied data.

The ordinary record view starts in source detail, so a one-shot file/stdin view
shows the original value even at compact widths. Explicit inspection profiles
also start in full-width detail, with the first retained record selected.
The ordinary record view accepts every JSON root. Grouping and contextual
metadata are optional. A generic view needs no session, conversation, or agent
identity. Legacy display headers remain a compatibility input rather than a
requirement of the inspection model.

Legacy three-header calls without `Inspection` retain their existing Simple
field projection and hidden-hit Verbose promotion. An explicit `Inspection`
opts into full-record inspection, including when legacy display headers are
also supplied. Simple and Verbose retain their string-preview bounds.

## Source and descriptors

The source is the original immutable `bytes`. Descriptors refer to its physical
JSONL source lines; they never replace the source with a display-oriented JSON
document. Filtering, grouping, folding, and section changes retain those source
identities. Synthetic labels and annotations are distinct from source content.

| Descriptor | Meaning |
| --- | --- |
| Context | Optional, inert display labels and values. |
| Group | A unique identity, label, and explicit source-record members. |
| Section | An arbitrary labeled subset of a group's members, with optional empty text. |
| Facet | A label, typed options, selection mode, and explicit item-value source. |
| Search scope | A label and explicit source targets with key/index and bounded JSON-decoding steps. |
| Annotation | Prepared display values attached to exact source items. |

A record belongs to at most one explicit group. Records outside groups remain
independent items. Members may be noncontiguous in the source, but retain source
order. A group can contain repeated event kinds and any number of sections;
there are no required event names or expected phases. Original members remain
accessible even if a section is empty or omits them.

Descriptors are materialized immutable values. They cannot contain predicates,
runtime objects, validators, executable expressions, or callbacks. Validation
covers references, duplicate membership, types, labels, metadata sizes, and
selector/decoding limits. The descriptor interface does not load application
configuration or discover presets.

Context labels and short labels allow 512 characters; Context values allow
2,048. This also preserves historical headers whose independently valid
512-character identity and subject combine into one 1,027-character display
value. Descriptor identifiers allow 128 characters. Inspection metadata has
an incremental 1 MiB budget, with at most 64 facets, 256 options per facet,
64 scopes, and 10,000 combined group-membership or scope-target references.
Oversized automatic facets are omitted with a notice rather than making an
otherwise valid source unviewable.

## Library example

This constructed audit example defines its own grouping and display vocabulary:

```python
from jsonl_viewer import ViewerSpec, view_jsonl
from jsonl_viewer.inspection import (
    Context, Facet, Group, Inspection, Option, SearchScope, Section, Target,
)

source = (
    b'{"trace":"a","kind":"open","level":"info","message":"ready"}\n'
    b'{"trace":"a","kind":"read","level":"error","message":"timeout"}\n'
    b'{"message":"independent record"}\n'
)
spec = ViewerSpec(
    context=(Context("Dataset", "Audit events"),),
    inspection=Inspection(
        groups=(Group(
            "trace-a", "Trace A", (1, 2),
            sections=(Section("events", "Events", (1, 2)),),
            values=(("severity", "error"),),
        ),),
        facets=(Facet(
            "severity", "Severity",
            (Option("info", "Info"), Option("error", "Error")),
            path=("level",),
        ),),
        scopes=(SearchScope(
            "messages", "Messages",
            tuple(Target(line, ("message",)) for line in (1, 2, 3)),
        ),),
    ),
)
view_jsonl(source, spec)
```

The third record stays independent. The explicit group's `severity` is caller
data; the viewer does not calculate a trace's severity from its events. A facet
path applies to independent record items. `Target` uses physical source lines;
`DECODE` is the explicit bounded step for a complete encoded JSON container.
`MISSING` is the distinct facet value for an absent path or group attribute.

## Filters and literal search

Filters select list items; search locates source occurrences inside those items.
Selecting a matching group keeps all of its related source records available.
It does not discard members that lack the keyword.

Within a facet, selected options are ORed. Different facets and the active
search are ANDed. All means no restriction. It cannot coexist with individual
options, and removing the last individual option returns the facet to All.
Single-select facets choose one condition at a time.

Facet comparison preserves JSON scalar types. Missing, `null`, an empty string,
`true`, and `1` are distinct. A container is not silently stringified into a
scalar option. Group facet values are prepared explicitly by the caller; the
viewer does not infer aggregate application states from group members.

Search text remains one bounded, case-insensitive literal phrase. Spaces are
part of the phrase. There is no field-query syntax, regular expression, or
evaluation language. Selecting a named scope changes its source targets, not
the meaning of the query.

The default all-text range retains original source search semantics, including
bounded decoded JSON and existing representation deduplication. It does not
search synthetic labels as if they occurred in the JSONL. A source property
named `schema` has no reserved meaning and remains searchable unless an
explicit range does not target it.

An intentionally untargeted record is different from a declared target that
cannot be decoded or exceeds a bound. Coverage reports the latter rather than
presenting it as an ordinary complete zero-match result. Selected occurrences
retain their original source reference, path, representation, and character
span. Folding and display-preview limits do not narrow the retained text
searched. Data absent from the snapshot cannot be recovered.

## Lists, sections, and counts

The default list contains records. When groups are supplied, it contains the
explicit groups together with independent ungrouped records. Labels and detail
sections come from descriptors. Full source identities remain available in
detail even when a compact list label is clipped.

Report total items, filter-eligible items, search-matching items, matching source
records, and occurrence counts separately. With no committed query, omit
occurrence counts rather than treating an unapplied search as zero matches.
Do not count a source occurrence again merely because another section can
display the same record.

Item traversal selects a record or group. `n`/`N` traverses individual search
occurrences and synchronizes the list, detail section, and focused source
position. Manual detail navigation can inspect related nonmatching data while
retaining the query. The next occurrence command reveals its next source hit.

Selecting a new item starts at its first retained record, or its first current
match while searching. Revisiting one of the 32 most recently visited groups
restores its record, section, cursor, folds, and scroll position. This bounded
invocation-only cache includes the current group and discards transient drag,
copy, and prompt state. Applied query, scope, or filter changes invalidate it;
layout, resize, focus, and list-name finding do not. An evicted group uses the
normal first-entry behavior. Enter on the unchanged item retains its position.
Empty configured sections are valid saved positions. Cross-item cursor, wheel,
source-line, and occurrence navigation also save the outgoing position; these
paths do not silently bypass the bounded cache.

Every item uses one row: selection/kind/number, name, then a fixed right-aligned
record-count column. `G` marks a caller-provided group and `R` an ungrouped source
record; opaque labels cannot disguise the item kind. With source search active the count is `matching/total`;
without it only the total is shown. List-find result counts are separate from
source match counts. Names containing wide glyphs, combining characters, or
line breaks never create extra list rows. Labels are safely neutralized and
cell-clipped without changing the original descriptor or record.

The selected information area exposes the full name, ID, and exact count;
`,`/`.` scroll its rows while list focus remains active. Repeated labels include
a short ID in the available name column. Exact IDs remain available in that
information area. Alternating full-row backgrounds follow original item order,
and the selected row uses blue, white text, and `▸`. Focus is identified in the
pane title, independently of selection. Search highlighting covers matching
label/ID text rather than recoloring the entire selected row.

In list focus, `t` edits a literal, case-insensitive name/ID substring. It only
narrows the existing filter/source-search result; it does not search synthetic
labels as source text, alter grouping, or discard nonmatching member records.
An already selected item outside the name results remains in detail with a
hidden-selection notice. Up/Down explicitly selects a visible result. Enter
retains a visible selection or selects the first visible result and opens detail;
with no result Enter/Up/Down preserve the current detail. Escape in the editor
clears name finding and returns to the list. After Enter closes the editor,
Escape first clears an active name query while preserving the current focus,
detail position, and applied source conditions. With no name query, list Escape
returns to detail as before. Other command letters, including `p`, remain
literal Unicode text in the editor.

## Filter drafts

Opening filters copies the committed conditions into a draft. Editing a field
does not update the list behind the panel. A visible draft marker conveys that
changes are not applied, independently of color.

| Action | Result |
| --- | --- |
| Open a field | Edit that field's local draft. |
| Space in options | Toggle a checkbox or select a radio option. |
| Finish a field | Return its draft to the panel without applying the whole panel. |
| Escape in options | Cancel only that field edit. |
| Apply | Commit the whole draft and calculate the new result atomically. |
| Reset filters | Draft All for filters, retaining the query and search scope. |
| Reset all | Draft empty query, All filters, and all-text scope. |
| Cancel or panel Escape | Discard drafts and restore committed selection and scroll. |

Reset actions require Apply. Reset all must visibly disclose that it will also
clear the query. Applying an unchanged draft can close without recalculation.
An invalid query, descriptor failure, or occurrence limit must not partially
replace committed conditions or results.

Retain the selected item if it remains eligible. Otherwise select the first
result in stable source order. A valid empty result commits the new conditions
and clears stale detail; it is not an input failure. Distinguish an empty
snapshot, no filter-eligible items, no text matches, and incomplete coverage.

Search editing follows the same draft/commit/cancel rule. While editing query
text, command letters are literal input. Existing cursor editing and Unicode
input behavior remain available.

## Main controls

| Input | Action |
| --- | --- |
| `/` | Edit the query and named scope. |
| `f` | Open the filter draft. |
| `t` in list | Edit literal name/ID finding without changing source search or filters. |
| `p` in main view | Cycle invocation-only Dark / Light / ANSI palette. |
| `,` / `.` in list | Scroll selected full-name/ID/count information. |
| Tab / Shift+Tab | Switch list/detail focus or move modal focus. |
| `v` | Switch Single/Split layout in the main view. |
| Up / Down in the list | Select an item. |
| Enter in the list | Open the selected item's current detail without resetting its position. |
| Up / Down in detail | Traverse eligible source records. |
| `[` / `]` | Select arbitrary detail sections. |
| `n` / `N` | Reveal the next/previous source occurrence. |
| `a` | Inspect prepared annotations, separate from source text. |
| `g` | Go to a positive original source line. |
| `h/j/k/l`, Enter in JSON | Retain existing JSON cursor and fold behavior. |
| `c` | Clear only the query, retaining filters and scope. |
| `?` / `q` | Show help / close the main view. |

The main footer keeps `Occurrence i/N` separate from total item/record/match
counts, including at narrow widths and while modal drafts are open. The query
editor's Scope row uses Left/Right to choose All text or a configured scope.
Enter on a filter field completes that field; Apply/Search on its action row
commits the whole corresponding draft. Escape inside a filter field cancels
that local edit; Escape at panel level cancels the entire panel.

Normal left-button dragging selects JSON body text in detail, in either Single
or Split. Hold at a body edge to extend the selection through continuous vertical
or horizontal scrolling; release to copy automatically and show the result in
the footer. Selection stays within the current section's displayed record order.
Dragging over the list/header does not activate it. Plain clicks and normal
wheel navigation retain their existing behavior. See [mouse selection and
navigation](../README.md#mouse-selection-and-navigation-080) for copy spelling,
local clipboard support, cancellation, and optional embedding capabilities.

## Geometry, input, and ownership

Single is the default layout: the focused list or detail uses the entire width,
regardless of terminal size. Tab or Shift+Tab switches screens, and Enter in the
list opens its detail. There are no separate click-switch buttons. Up/Down selects
items in the list and traverses eligible source records in detail.

Press `v` in the main view to choose Split. At least 120 columns and 12 rows show
the existing left list beside detail; the list targets 40 cells and can grow to 48 for long labels, staying within
32–48 cells while preserving the existing detail minimum
and remains visible while detail has focus. Smaller terminals show only the
focused screen without losing the Split preference; widening restores the split.
The existing footer shows the selected layout and shortcut without another row.

Layout and focus changes preserve the selected record, section, JSON cursor,
folds, scroll position and committed conditions. Resize reflows the current view
without resetting those conditions or modal drafts. The letter `v` remains literal
text while editing a search; modals do not change layout. Layout preference is
discarded on close, so each new view starts in Single/detail. Panels and option
lists scroll internally;
insufficient geometry must not commit a draft automatically.

Keep active-filter counts visible even when the full summary does not fit.
Focus, checkbox, radio, draft, match, and error states need printable cues in
plain and `NO_COLOR` output. Unicode cell widths and existing source/display
line identities remain authoritative.

Keyboard and mouse navigation/filter actions have equivalent effects. Clicking
outside a modal does not discard its draft. These actions remain available
without a mouse; drag selection and automatic copying use the mouse protocol.

## Independent acceptance

Verify the same controls with ordinary log and audit-event data in an isolated
interpreter. Include all JSON root types, groups with repeated kinds and four
sections, two independent ungrouped records, a real `schema` property, typed
Missing/null/empty values, bounded encoded JSON, label/value substitution,
invalid descriptors, original-source hashes and references, cancellation,
zero matches, mouse/keyboard, Unicode, resize, and plain rendering.

Passing an embedding application's examples alone does not establish this
contract. Generic behavior, supported imports, dependency boundaries, packaged
artifacts, and compatibility calls need independent evidence.
