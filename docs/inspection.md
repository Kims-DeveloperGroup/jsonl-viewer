# Generic inspection

Inspection adds a record list, optional groups and sections, typed filters, and
named search ranges to one immutable JSONL snapshot. It does not define an
application lifecycle. A log level, backend name, workflow state, or section
label is ordinary caller-supplied data.

The ordinary record view starts in source detail, so a one-shot file/stdin view
shows the original value even at compact widths. Explicit inspection profiles
start with list focus. The ordinary record view accepts every JSON root. Grouping and contextual
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
| Tab / Shift+Tab | Switch list/detail focus or move modal focus. |
| Up / Down in the list | Select an item. |
| Enter in the list | Open its detail or first current match. |
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

## Geometry, input, and ownership

At 120 terminal cells and above, show list and detail side by side. At narrower
widths, show one region at a time with focus switching. Resize preserves
committed state and modal drafts. Panels and option lists scroll internally;
insufficient geometry must not commit a draft automatically.

Keep active-filter counts visible even when the full summary does not fit.
Focus, checkbox, radio, draft, match, and error states need printable cues in
plain and `NO_COLOR` output. Unicode cell widths and existing source/display
line identities remain authoritative.

Keyboard and mouse actions have equivalent effects. Clicking outside a modal
does not discard its draft. Every action is available without a mouse. The
existing host owns transport and terminal resources; inspection does not enter
another raw mode, acquire another screen, or create a second input lifecycle.
Closing drops view state and restores the existing ownership boundary.

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
