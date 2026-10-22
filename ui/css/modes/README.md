# UI presentation modes

Existing CSS filenames remain compatibility entry points. Their ordered imports
delegate rules to these folders:

- `shared/`: tokens, components and full-interface rules shared by desktop and
  mobile, plus responsive rules that also affect compact windows.
- `mini/`: explicit `.iru-mini` presentation.
- `mobile/`: existing width queries at 768px and below, with their original
  conditions intact. These queries may also match a narrow native mini window.
- `desktop/`: existing explicit desktop width queries.

Numeric suffixes preserve each source file's cascade sequence. Do not combine
all mode folders into one import per mode: that would reorder overrides.
The order in `ui/style.css` is also preserved.

This is a mechanical stylesheet extraction, not a new mode detector. Mini stays
an explicit shell class; desktop/mobile remain responsive layouts. Shared rules
using `html:not(.iru-mini)` intentionally continue to cover desktop AND mobile.
DOM, JavaScript, native window geometry and application state are unchanged.

Relative asset URLs are rebased two levels to keep the same targets.
For later visual edits, change the delegated fragments, not the entry points.
Keep conditions and selector scope explicit when changing a single mode.

Validation: reconstructed CSS was compared with the base revision, preserving
rule text and sequence after URL rebasing and newline normalization. No tests,
browser/LIVE checks or screenshots were run as part of this extraction.
