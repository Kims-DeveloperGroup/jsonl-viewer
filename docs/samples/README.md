# Deterministic terminal samples

These plain UTF-8 files are complete, source-controlled renderer frames:

- `simple.txt`
- `verbose.txt`
- `search-matches.txt` — one focused keyword occurrence, other matching record
  markers, and an occurrence count that includes keys and values
- `record-navigation.txt` — first-record notice after wrapping backward and forward
- `sibling-navigation.txt` — key focus and endpoint notice after a circular sibling jump
- `cursor-idle.txt` — hidden caret phase with unchanged reserved row
- `cursor-folding.txt` — focused opening delimiter and a folded record object
- `folded-search.txt` — manual folding hides the active hit while retaining its occurrence count
- `nested-expanded-json.txt` — recursively expanded provider content and
  response text with visible derived-display cues
- `truncated-content.txt`
- `truncated-nested-leaves.txt` — an expanded container whose long string leaf
  is independently previewed while structure remains visible
- `malformed-input.txt`
- `tiny-terminal.txt`
- `plain-no-color.txt`

`tests/test_documented_samples.py` reconstructs every frame through the public
API and compares it byte for byte. ANSI is code-native rather than stored as
opaque terminal bytes: the renderer's SGR role table is documented in the
[design system](../design-system.md), and tests verify the same JSON and focus semantics in both modes. Plain
frames reserve a caret row; ANSI frames use inverse video on the character
itself, so their visible data-row counts intentionally differ.

The sample files are intentionally not screenshots. They remain searchable,
reviewable, accessible without color, and byte-synchronized with the behavior
that produces them.

Samples use the visible cursor phase unless their name specifies otherwise.
Idle-phase verification keeps the same caret row and data geometry; interactive
blinking is driven by host idle events, not wall-clock sampling of snapshots.
