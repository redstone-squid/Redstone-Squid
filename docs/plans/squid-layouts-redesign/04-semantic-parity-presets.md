# 04 — Close semantic parity gaps, retire `primitives.presets`

Depends on plan 03 (factory layer): the phase-B migration below is written factory-style
and relies on its `None`-filtering. Land 03 first so the nine consumer files migrate once.

## Context

`primitives/presets.py` (`card`/`banner`/`listing`/`report`) predates the semantic
redesign and is kept alive purely because nine production bot files still call
`sl.primitives.card()` inside live `render()` methods. Component-based ≠
semantic-vocabulary-authored. Nobody migrated them because `semantic.Fields`, `Section`,
and `Media` are missing three capabilities `presets.card()` has always had:

1. **Per-field fallback ladders.** `presets.Field.alts` supplies shorter alternates for
   one value. `semantic.Field` has no equivalent; `Fields` lowering
   (`planning/adaptation.py:173-182`) joins plain f-strings into one
   `Lines(..., overflow=Never())` — a whole-block, all-or-nothing policy.
2. **Custom accent color.** `Aside` maps `tone` to 4 fixed colors (`_tone_color`,
   `planning/adaptation.py`); `Section`/`Article` lower to a bare uncolored `Panel`
   (`planning/adaptation.py:157-162`). A caller-chosen brand color (e.g.
   `starboard/render.py`'s `config.colour`) has no semantic expression.
3. **Thumbnail-beside-heading.** `presets.card`'s `media` puts `media[0]` in a
   `primitives.Section(texts=..., accessory=Thumbnail(...))` and the rest in a `Gallery`.
   `semantic.Media`'s `FEATURED` display only ever produces a `Gallery`.

Critical solver finding from the audit: `Lines.overflow=Never()` does **not** consume
per-entry `Alt` ladders — `_apply`'s `Never()` branch head-trims the pre-joined string.
Only `Spill()` steps ladders, and `Spill` also drops whole entries, which would break
`Fields`' "never loses a whole field" guarantee. Capability 1 therefore needs a new
overflow policy, not a new argument.

## Design — phase A, framework additions

**New overflow policy `Ladder`** in `primitives/constraints.py`: steps each `Lines`
entry down its own `Alt` ladder under pressure exactly like `Spill`, but never drops an
entry; when every entry is at its shortest rung and it still does not fit, head-trim the
joined result (mirrors `Never`'s final fallback). Wire into `planning/solve.py`:

- `_allocate`: treat `Ladder` as fixed-cost alongside `Never`
  (`isinstance(unit.overflow, Never | Ladder)`) so `Fields` cannot be starved by
  flexible-priority sharing — matches current behavior.
- `_apply`: add `case Ladder() if usable >= 1:` reusing the stepping loop from
  `_apply_spill` minus the drop loop — factor the stepping logic into a shared helper
  used by both rather than duplicating it. (Plan 05 works on the component axis and does
  not collide; the shared helper is the seam both build on.)

**`semantic.Field`**: add `fallbacks: tuple[TextLike, ...] = ()`.

**`Fields` lowering**: per field with fallbacks, build
`Alt(f"**{label}:** {value}", tuple(f"**{label}:** {fb}" for fb in fallbacks))`;
plain string otherwise; switch the `Lines` overflow from `Never()` to `Ladder()`.

**`semantic.Section`/`Article`**: add `accent: Color | None = None`,
`footer: TextLike | None = None`, `media: str | None = None` (lead image URL — a
singleton accessory needs no reconciliation key). Importing `Color` from
`primitives.styles` is a deliberate, contained layering breach (semantic.py already
imports the `PrimitiveNode` union member). **Keep it on a leash**: the docstring must
frame `accent` as an explicit brand override with `Aside`+`tone` as the preferred
semantic path; if `accent=` becomes the default way things get colored, the semantic
layer's neutrality claim dies.

**Lowering** (`planning/adaptation.py:157-162`):

- `accent` passes through to `Panel(tuple(contents), accent=accent)`;
- `footer` appends a primitive `Footer(resolve_text(footer).content)` last;
- `media` replaces the bare heading with
  `primitives.Section(texts=(heading_prim,), accessory=Thumbnail(media))` — heading
  beside thumbnail, everything else flows below. Deliberately simpler than presets'
  "body beside thumbnail too": guessing which child is "the body" from an arbitrary
  children tuple is fragile.

**Accepted behavior change**: `build_handler.py` is the only `FieldGroup`/`groups=`
caller; its `_group_ladder` steps a titled group's fields in lockstep. Migrating to a
nested section gives each field independent `Ladder` stepping — finer granularity, not a
regression. Call it out in that file's migration commit message.

**Tests**: `test_solve.py`/`test_limits.py` for `Ladder` stepping and exhaustion-trim;
`test_semantic_structures.py`/`test_alts.py` for `Fields` fallbacks and `Section`
accent/footer/media; `test_public_api.py` for new exports.

## Design — phase B, migrate the 9 consumers

Same shape in every file; representative sites: `squid/bot/settings_view.py:435,485`,
`squid/bot/account_view.py:362-365`, `squid/bot/consent.py:68-70`,
`squid/bot/notifications_view.py:209-217`, `squid/bot/voting/poll_wizard.py:469-483`,
`squid/bot/submission/build_handler.py:138-163`,
`squid/bot/submission/ui/views.py:513-542,1036-1101,1230`,
`squid/bot/layout_showcase.py:158-239`.

- `sl.primitives.card(title, description, accent=..., fields=[...], footer=..., media=[...], rows=[...])` →

      sl.section(
          description and sl.paragraph(description),
          sl.fields(sl.field(key, label, value, fallbacks=...) for ...),
          *rows,                      # raw primitives.Row children type-check as LayoutNode
          heading=title, accent=accent, footer=footer,
          media=media[0] if media else None,
      )

  Extra media beyond the first becomes a trailing `sl.media(...)`/`sl.Media` sibling
  with a key.
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
