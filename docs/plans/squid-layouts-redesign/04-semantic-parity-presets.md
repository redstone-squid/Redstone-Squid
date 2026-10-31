# 04 — Close semantic parity gaps, retire `primitives.presets`

Depends on plan 03 (factory layer, landed): the phase-B migration below is written
factory-style and relies on its `None`-filtering.

## Context

`primitives/presets.py` (`card`/`banner`/`listing`/`report`) predates the semantic
redesign and is kept alive purely because production bot files still call
`sl.primitives.card()` inside live `render()` methods. Component-based ≠
semantic-vocabulary-authored. Nobody migrated them because `semantic.Fields` and
`Section` are missing four capabilities `presets.card()` has always had:

1. **Per-field fallback ladders.** `presets.Field.alts` supplies shorter alternates for
   one value. `semantic.Field` has no equivalent; `Fields` lowering joins plain
   f-strings into one `Lines(..., overflow=Never())` — a whole-block, all-or-nothing
   policy.
2. **Custom accent color.** `Aside` maps `tone` to four fixed colors (`_tone_color`);
   `Section`/`Article` lower to a bare uncolored `Panel`. A caller-chosen brand color
   (`squid/bot/ui.py`'s `DISCORD_*` palette, `starboard/render.py`'s `config.colour`)
   has no semantic expression.
3. **Footer text.** There is no semantic node for a card's small print at all.
4. **Thumbnail-beside-heading.** `presets.card`'s `media` puts `media[0]` in a
   `primitives.Section(texts=..., accessory=Thumbnail(...))`. `semantic.Media`'s
   `FEATURED` display only ever produces a `Gallery`.

Critical solver finding from the audit: `Lines.overflow=Never()` does **not** consume
per-entry `Alt` ladders — `_apply`'s `Never()` branch head-trims the pre-joined string.
Only `Spill()` steps ladders, and `Spill` also drops whole entries, which would break
`Fields`' "never loses a whole field" guarantee. Capability 1 therefore needs a new
overflow policy, not a new argument.

## Design decisions taken on re-review

Three places where this plan now departs from its first draft, with the reasoning, so
the departure is not silently re-litigated:

- **The new overflow policy is named `Condense`, not `Ladder`.** Every other member of
  `Overflow` names the *behaviour* (`Truncate`, `Spill`, `Paginate`, `Drop`, `Never`);
  `Alt`/`Alts` already name the *data*. `Ladder` would be the only policy named after
  the data it consumes, and would read as a third sibling of `Alt`/`Alts`.
- **Footers become a semantic node `Note`, not a `footer=` keyword on `Section`.** A
  footer is content, and the factory doctrine from plan 03 is "content is positional".
  `Figure(caption=)` already lowers to a primitive `Footer`, so the concept exists;
  making it a node lets small print appear in an `Aside`, a `Details`, or anywhere else
  rather than only at a section's foot. One node replaces a keyword that would have had
  to be duplicated onto `Article`.
- **`Section.media` is named `thumbnail`, and the accent stays a raw `Color`.** A
  tone→color `Palette` on `Chrome` was considered and rejected: it does not cover the
  case that actually forces the issue (starboard's per-guild `config.colour` is data,
  not policy), so the raw-color escape hatch is needed regardless, and shipping both
  would be two mechanisms for one job. `Aside`+`tone` remains the path for color that
  *means* something; `Section.accent` is house chrome and is documented as such.

## Design — phase A, framework additions

**New overflow policy `Condense`** in `primitives/constraints.py`: steps each `Lines`
entry down its own `Alt` ladder under pressure exactly like `Spill`, but never drops an
entry; when every entry is at its shortest rung and it still does not fit, head-trim the
joined result (mirrors `Never`'s final fallback). A `Lines` whose entries are plain
strings therefore degenerates to `Never` — which is what `Fields` without fallbacks
should do. Wire into `planning/solve.py`:

- `_allocate`: treat `Condense` as fixed-cost alongside `Never`
  (`isinstance(unit.overflow, Never | Condense)`) so `Fields` cannot be starved by
  flexible-priority sharing — matches current behavior. Consequence to state in the
  docstring: a `Condense` node's ladders only engage once the *fixed* share overdraws
  the budget, not merely because some flexible node wants room.
- `_apply`: add `case Condense() if usable >= 1:` reusing the stepping loop from
  `_apply_spill` minus the drop loop — factor the stepping into a shared
  `_step_ladders` helper used by both rather than duplicating it. (Plan 05 works on the
  component axis and does not collide; the shared helper is the seam both build on.)

**`semantic.Field`**: add `fallbacks: tuple[TextLike, ...] = ()`.

**`Fields` lowering**: per field with fallbacks, build
`Alt(f"**{label}:** {value}", (f"**{label}:** {fb}", ...))`; plain string otherwise;
switch the `Lines` overflow from `Never()` to `Condense()`. Ladder rungs come from user
data, so reuse presets' forgiving normalization (drop empty rungs and rungs longer than
what precedes them) rather than letting `Alt.__post_init__` raise on a build whose
"shorter" form happens to be longer.

**New semantic node `Note(content)`** — small print — lowering to
`primitives.Footer(content, overflow=Never())`, consistent with every other prose node.

**`semantic.Section`/`Article`**: add `accent: Color | None = None` and
`thumbnail: str | None = None` (lead image URL — a singleton accessory needs no
reconciliation key). Importing `Color` from `primitives.styles` is a deliberate,
contained layering breach (semantic.py already imports the `PrimitiveNode` union
member). **Keep it on a leash**: the docstring must frame `accent` as an explicit brand
override with `Aside`+`tone` as the preferred semantic path.

**Lowering**:

- `accent` passes through to `Panel(tuple(contents), accent=accent)`;
- `thumbnail` with a heading replaces the bare heading with
  `primitives.Section(texts=(heading_prim,), accessory=Thumbnail(url))` — heading beside
  thumbnail, everything else flows below. Deliberately simpler than presets' "body
  beside thumbnail too": guessing which child is "the body" from an arbitrary children
  tuple is fragile.
- `thumbnail` with no heading has nothing to sit beside, so it lowers to a leading
  `Gallery((url,))`. Documented, not an error.

**Accepted behavior changes**, both to be called out in the migrating commits:

- `build_handler.py` is the only `FieldGroup`/`groups=` caller; its `_group_ladder`
  steps a titled group's fields in lockstep. Migrating to a nested section gives each
  field independent `Condense` stepping — finer granularity, not a regression.
- `presets.card(fields=...)` renders `**name**\nvalue` (two lines); `semantic.Fields`
  renders `**name:** value` (one line, matching what `presets` groups already did).
  Cards that pass `fields=` change appearance slightly.

**Tests**: `test_solve.py`/`test_limits.py` for `Condense` stepping and exhaustion-trim;
`test_semantic_structures.py`/`test_alts.py` for `Fields` fallbacks, `Note`, and
`Section` accent/thumbnail; `test_factories.py` and `test_public_api.py` for the new
builders and exports.

## Design — phase B, migrate the consumers

Twelve files, not the nine first counted (`claims_view.py`, `search_view.py` and
`diagnostics_view.py` were missed):

`squid/bot/ui.py`, `settings_view.py`, `diagnostics_view.py`, `notifications_view.py`,
`account_view.py`, `claims_view.py`, `consent.py`, `layout_showcase.py`,
`voting/poll_wizard.py`, `submission/build_handler.py`, `submission/search_view.py`,
`submission/ui/views.py`.

Same shape in every file:

- `sl.primitives.card(title, description, accent=..., fields=[...], footer=..., media=[...], rows=[...])` →

      sl.section(
          description and sl.paragraph(description),
          sl.fields(sl.field(label, value, fallbacks=...) for ...),
          extra_media and sl.media(*extra_media, key="media"),
          footer and sl.note(footer),
          *rows,                      # raw primitives.Row children type-check as LayoutNode
          heading=title, accent=accent,
          thumbnail=media[0] if media else None,
      )

- `sl.primitives.banner(content, accent=...)` → `sl.status(content, tone=...)` where a
  fixed tone fits; `sl.section(sl.paragraph(content), accent=accent)` where the exact
  color must survive (e.g. `DISCORD_BLUE`).
- `squid/bot/ui.py`'s `card_layout`/`banner`/`text_layout`/`link_layout` (and the
  `error_layout`/`warning_layout`/`info_layout`/`help_layout` wrappers) keep their exact
  external signatures — only the internal node construction changes; no call-site changes
  elsewhere in the bot.
- `build_handler.py` `groups=` → nested `sl.section(sl.fields(...), heading=group.title)`
  per group, per the accepted behavior change.

Framework changes in phase A land as their own commit(s); then one commit per consumer
file (small, cohesive, independently valid).

## Design — phase C, delete

- Remove `primitives/presets.py` and its exports from `primitives/__init__.py`
  (`FieldGroup`, `banner`, `card`, `listing`, `report` — `listing`/`report` already have
  zero callers).
- Delete `tests/test_presets.py`; rewrite any presets-based fixtures in `test_alts.py` /
  `test_pagination.py` onto semantic equivalents instead of deleting coverage.
- Update `docs/squid-layouts-architecture.md`, the package README, and
  `docs/plans/squid-layouts-migration.md` to drop the "legacy card fields under presets"
  framing.

## Verification

- After phase A: full package suite (`cd packages/squid-layouts && uv run pytest
  --no-cov` — the solver change is central enough) plus `just typecheck`.
- After each phase-B file: that file's unit module under `tests/unit/bot/` with
  `--no-cov`.
- After phase C: `just typecheck`, package suite, `tests/unit/bot` once,
  `git diff --check`.
- Visually sanity-check one migrated card (settings panel or `/layout demo`) via the
  `run` skill — rendering changes need an actual look.
