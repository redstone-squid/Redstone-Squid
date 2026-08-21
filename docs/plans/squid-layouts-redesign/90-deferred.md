# 90 — Deferred and rejected

Findings from the 2026-08-21 audit (full package + nine consumers + CascadeUI
comparison) that we consciously decided **not** to act on, with the reasoning — so they
are not re-derived or accidentally adopted later.

## Rejected

- **Redux-style global store** (CascadeUI's cross-view model: dispatch → middleware →
  reducers → subscribers). The local `Component + state() + computed + transaction`
  model is simpler and fits the frontend-neutral tree. Cross-view updates already have a
  path: shared services + `Reactor.schedule`/`Mount.refresh`. If a real many-views-one-
  domain need appears, add a host-side event bus, not a store in the package.
- **Persistence batteries** (SQLite/Postgres `SnapshotStore` implementations,
  reattachment, pruning). The durability layer has **zero production consumers** in
  `squid/` (verified by grep). Building storage backends for an unused subsystem is
  inventory. Revisit only when a view actually needs to survive restarts; the
  `LeaseSnapshotStore` boundary is ready when that day comes.
- **`compose(into=view)` / adopting existing discord.py views** — re-confirmed: renderer
  ownership is what keeps budget measurement sound. Incremental interop is CascadeUI's
  advantage by design choice, not an oversight here.
- **Context-manager render DSL** (dominate-style) — fights `render()`-returns-a-value
  purity; the factory layer (plan 03) is the chosen ergonomics fix.
- **Python 3.10 backport / PyPI packaging** — irrelevant to this repo (3.14 target).
  Publishing squid-layouts is a product decision to make explicitly, not design debt.

## Deferred until a real consumer exists

- **Portable permission facts on `ActionEvent`** — plan 02 gives the typed Discord
  escape hatch instead. If a second frontend ever dispatches events, design the portable
  capability surface against its actual requirements.
- **Ephemeral session handoff** (Cascade-style: arm a refresh control before token
  expiry, rebuild the session from the fresh interaction). Mostly retired: plan 07's
  `EditHandle` renews on every click, so an ephemeral panel in use stays writable
  indefinitely. What remains is an ephemeral view that needs a *background* refresh after
  more than 15 minutes with nobody touching it — the render simply waits in `Mount.pending`
  until someone does. Only worth building for a view that must update itself unattended,
  which none does.
- **Participant tracking / shared sessions** — plan 12 v1 ships instance policies and
  `allowed_users`; participant lifecycle waits for a feature that needs it.
- **`squid_layouts.patterns` library** (Form, Wizard, richer table/list browser à la
  CascadeUI's pattern modules). Likely valuable — the poll wizard and submission form
  are hand-rolled wizards today — but premature before plans 03/04 settle the authoring
  surface they would be built on. Revisit after the presets migration lands.
- **`sl.resource` descriptor** (declared `pending | ready | failed` state with
  `.reload()`), cut from plan 09. Under awaited `on_load` the pending state is never
  observable at first paint, and without a dependency model it worsens its motivating
  consumer: `SettingsPanel` fetches `_preset`/`_weights` as a function of `self._kind`,
  which needs declared deps and an optimistic set before `.reload()` beats the explicit
  `open_voting` method. Revisit only with a dependency design (declared deps or tracked
  reads during the fetch), plus a staleness guard for out-of-order reloads.
- **Portable form protocol** (replacing the Discord-native modal boundary) — long-noted
  in the architecture doc's gaps; unchanged priority.
- **Cross-page multi-select** — still rejected pending an explicit grouping/commit
  model, per the documented boundary.
