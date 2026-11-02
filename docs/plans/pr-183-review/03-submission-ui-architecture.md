# Submission UI Architecture

## Findings

- `EDIT_FIELDS` now centralizes edit-field applicability and placeholders, addressing some repeated modal construction, but submission basics/details still construct each input and label manually.
- Labels are still represented both by `discord.ui.Label` and field metadata; `BuildField` also has a large constructor and relies on string attribute names plus runtime type checking.
- Comma splitting and `strip()` remain local to the view. URL validation, Discord limits, and presentation colours are only partially centralized.
- The two draft selects duplicate parent-refresh logic and use `hasattr`/type ignores. Their domain mutation and view coordination are tightly coupled.

## Intended changes

- Define typed field specifications for both creation and editing: domain patch key, translated label/placeholder, parser/formatter, requiredness, Discord length/style constraints, and category applicability. Generate inputs and labels from that single source.
- Replace the wide `BuildField` constructor with factories from those specs, and have modal submission produce a typed draft/patch result instead of mutating arbitrary attributes by string.
- Move reusable input parsing into a submission-input module: trimmed optional text, comma-separated values, and URL lists with per-value errors. Keep domain-specific parsers beside their domain behavior.
- Introduce a Discord presentation colour enum/value type and make layout builders enforce Discord character, option, component, and media limits at their boundaries.
- Give draft controls a small typed parent protocol or callback instead of probing `hasattr`; fix the `DirectonalityLocationalitySelect` spelling while touching its interface.

## Tests

- Table-driven tests instantiate every field spec, prove label/input metadata has one source, and round-trip parser/formatter behavior.
- Boundary tests cover Discord maximum lengths/counts and deterministic truncation or validation errors.
- Modal/select tests assert typed patch results and refresh behavior without inspecting brittle raw component trees.
