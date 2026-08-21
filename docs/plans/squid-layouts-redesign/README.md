# squid-layouts redesign series

Design-debt fixes for `packages/squid-layouts`, agreed 2026-08-21 while the framework is
still on the `local-development` feature branch. Each numbered file is one independently
landable unit of work with its own verification section; this file fixes the order and
the reasons for it.

The audit behind these plans covered the whole package (~8k lines), the nine production
consumers, and a comparison against CascadeUI
(https://github.com/HollowTheSilver/CascadeUI). Everything found is captured: plans
01–07 are the dependency-ordered core, 08–17 are agreed improvements whose order is
flexible, and `90-deferred.md` records what was consciously rejected or postponed so it
is not re-derived later.

## Core sequence (dependency-ordered)

| # | Plan | Why this position |
|---|------|-------------------|
| 01 | [Mount delivery atomicity](01-mount-atomicity.md) | Correctness bug (failed Discord edit bricks EXCLUSIVE controls), small, no design dependencies. First. |
| 02 | [Typed native event access](02-native-event-access.md) | Deletes an anti-pattern already copied into six files; unblocks migrating remaining old views without spreading it. |
| 03 | [Factory layer for composition](03-factory-layer.md) | Small, additive. Must land before 04: 04's migration snippets are factory-style and rely on `None`-filtering that does not exist today. |
| 04 | [Semantic parity, retire presets](04-semantic-parity-presets.md) | Closes the capability gaps (`Ladder`, accent, footer, lead media) keeping nine files on `primitives.presets`, migrates them, deletes the module. With 03, attacks the 15:1 semantic-bypass ratio from both ends. |
| 05 | [Variant ladders](05-variant-ladders.md) | `Fold` and `Choice` merge into one `Variants` node; self-contained solver change that simplifies the collapse loop before 06 reworks the same code. |
| 06 | [Pagination](06-pagination.md) | One cursor ladder under three slicers; the page index becomes a projection, so planning owns reconciliation and the mount draws once. Retires 01's cursor-snapshot workaround. |
| 07 | [`EditHandle`](07-ephemeral-lifetime.md) | The mount holds a way to write to its message, not a message. Closes the `notice()`-clobbers-the-panel bug and makes ephemeral refresh renew on every click; any time after 01. |

## Second tier (order flexible)

| # | Plan | Summary |
|---|------|---------|
| 08 | [Transactional state coverage](08-transactional-state-coverage.md) | Undeclared writes are reported rather than half-tracked: PARALLEL_READ rejects them, `sl.strict_state()` raises. `sl.state()` gains a no-initial form, `copy="ref"`, and typed overloads; the panels declare their view state. Shipped. |
| 09 | [Async data loading](09-async-data-loading.md) | `on_load` hook replaces every hand-rolled `load()`-before-mount, awaited inside 15's send seam. Expansion stops at a component that still owes a load, so `render()` never runs before `on_load`. Also retired the three superseded `ExpiringLayoutView` panels, fixing a live defect where the settings modals redrew through the dead compat layout. The `sl.resource` descriptor moved to `90-deferred.md` pending a dependency model. Shipped. |
| 10 | [Selection ownership](10-selection-ownership.md) | One rule for selected/opened/open: every stateful node takes `sl.controlled(value, on_change)` or `sl.managed(initial)`. Ownership is a value, not an inference from which fields were passed; the `None`-sentinel version is recorded as rejected. Shipped. |
| 11 | [Small warts sweep](11-small-warts.md) | Vestigial `Composition.interventions`, `Choices(minimum=1)` audit, custom-id collision test, post-01 `build_view` naming. Shipped. |
| 12 | [Session policy wrapper](12-session-policy.md) | Host-side `MountRegistry` keyed by `SessionKey`, with REPLACE/REJECT instance policies and `parent=` cascade; `Mount.on_finish`/`finished` and a set-valued `lock_to` are the framework side. Coexist dropped — an optional key already expresses it. After 15, whose `Destination` seam makes replace-only-on-successful-delivery enforceable. Shipped. |
| 13 | [Runtime devtools](13-devtools.md) | Owner-only `!dev ui` cog over `sl.discord.mounts()`, a weak registry of live mounts: list, inspect, scene dump. `Mount.snapshot()` is the diagnostics contract; `Mount.address` and a retained `plan` are what make it complete. Development mode only. Shipped. |
| 14 | [Routed actions](14-routed-actions.md) | First-class stateless controls (`Route` + `RoutedAction` + `Router`): replaces the five hand-rolled `DynamicItem` classes and the `RawItem`/cast splices, makes routed scenes serializable, and unblocks semantic authoring of vote/starboard/consent cards. Routes keep their existing custom ids, so posted messages survive. Explicitly *not* a durability feature. Shipped; extended by 16. |
| 15 | [Send ownership](15-send-ownership.md) | `Mount.send(Destination)` runs stage→deliver→commit framework-side; `reply_to`/`respond_to` adapters own the discord.py kwargs, `ui.destination` keeps audience policy host-side in the existing `Visibility` vocabulary. `bind` is deleted — its four remaining callers were hand-rolled flushes. Amends 01 §6 ("bind is the commit point"); before 09. Shipped. |
| 16 | [Routed actions, part two](16-routed-actions-part-two.md) | Extends routed actions with exact converter-aware identity and aliases, the reserved `r:` namespace and gone responses, explicit stateless selects, portable `route_id`, and private route-table introspection. Stages 0-4 and 6-7 shipped; middleware/defer remains deliberately deferred because Discord interactions do not provide HTTP-like response semantics. |
| 17 | [Deferred text](17-deferred-text-i18n.md) | The framework translates: a `Message` carries its own msgid through the tree and is resolved at plan time by the mount's `Localization`. Retires `t(self.locale, _("…"))` (712 sites) in favour of `L(t"…")`, makes `Chrome` a locale-free constant so the twelve chrome-less `render_static` calls stop rendering English, routes interpolation through `md()`'s escaping, and lets `Mount.localize` retranslate a live panel. Needs a custom Babel extractor — the stock one drops `L(t"…")` silently. Capability plus a three-module pilot; the rest migrates opportunistically. |
| 18 | [Forms](18-forms.md) | `FormSpec` value core + `sl.Form` descriptor sugar; modal submission finally dispatches `SubmitEvent` through the mount funnel; framework-owned validation retry loop (modal-after-modal-submit is API-forbidden, verified). Executes plan 02's reserved `ModalSpec`-promotion move; retires the seven `ErrorHandledModal` subclasses. |
| 19 | [Patterns on forms](19-patterns.md) | The two-shell rule (pure `state → tree` core; component and router shells with control construction injected), `Wizard` with computed steps and orphan retention, `MultiChoicePanel` as the first consumer of Form's commit model — resolving 90's cross-page multi-select rejection. After 18. |
| 20 | [Solver pressure](20-solver-pressure.md) | Glue budgets (`min`/`prefer`/`stretch`), break annotations (`unbreakable`, `keep_with_next`), balanced breaking, region pagination, `sl.paged` sugar. Agreed policy: implementation may be ugly, contracts may not; one global solver redesign once the warts are visible. |
| 21 | [Cursor sources](21-cursor-sources.md) | `PositionPolicy` extracted from `PageBroker` as a pure precedence function; `WindowSource` with declared capabilities and capability-gated chrome; window-scoped staleness with source-defined fallback. Fetch stays pattern-side until the dependency model (shared with `sl.resource`) is designed. |

## Relation to existing plans

`docs/plans/squid-layouts-migration.md` remains the tracker for deleting superseded
discord.py view classes. Its `primitives.presets` bullet is superseded by plan 04 here;
its "core design debts closed" list still describes the landed state this series builds
on.
