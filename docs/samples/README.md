# Deterministic terminal samples

These plain UTF-8 files are complete, source-controlled renderer frames:

- `simple.txt`
- `verbose.txt`
- `search-matches.txt`
- `truncated-content.txt`
- `malformed-input.txt`
- `tiny-terminal.txt`
- `plain-no-color.txt`

`tests/test_documented_samples.py` reconstructs every frame through the public
API and compares it byte for byte. ANSI is code-native rather than stored as
opaque terminal bytes: the renderer's SGR role table is documented in the
[design system](../design-system.md), and tests require stripping those SGR
sequences to produce the exact corresponding plain frame.

The sample files are intentionally not screenshots. They remain searchable,
reviewable, accessible without color, and byte-synchronized with the behavior
that produces them.
