# squid-layouts redesign series

Design-debt fixes for `packages/squid-layouts`, agreed 2026-08-21 while the framework is
still on the `local-development` feature branch. Each numbered file is one independently
landable unit of work with its own verification section; this file fixes the order and
the reasons for it.

The audit behind these plans covered the whole package (~8k lines), the nine production
consumers, and a comparison against CascadeUI
(https://github.com/HollowTheSilver/CascadeUI). Everything found is captured: plans
01–07 are the dependency-ordered core, 08–15 are agreed improvements whose order is
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
| 09 | [Async data loading](09-async-data-loading.md) | `on_load` hook replaces every hand-rolled `load()`-before-mount, awaited inside 15's send seam. After 15. The `sl.resource` descriptor moved to `90-deferred.md` pending a dependency model. |
| 10 | [Selection ownership](10-selection-ownership.md) | One rule for selected/opened/open: every stateful node takes `sl.controlled(value, on_change)` or `sl.managed(initial)`. Ownership is a value, not an inference from which fields were passed; the `None`-sentinel version is recorded as rejected. Shipped. |
| 11 | [Small warts sweep](11-small-warts.md) | Vestigial `Composition.interventions`, `Choices(minimum=1)` audit, custom-id collision test, post-01 `build_view` naming. |
| 12 | [Session policy wrapper](12-session-policy.md) | Host-side `MountRegistry` with Replace/Reject/Coexist instance policies; `allowed_users` on Mount. |
| 13 | [Runtime devtools](13-devtools.md) | Owner-only `/dev ui` cog over a weak mount registry: list, inspect, scene dump. |
| 14 | [Routed actions](14-routed-actions.md) | First-class stateless controls (`Route` + `RoutedAction` + `Router`): replaces the five hand-rolled `DynamicItem` classes and the `RawItem`/cast splices, makes routed scenes serializable, and unblocks semantic authoring of vote/starboard/consent cards. Routes keep their existing custom ids, so posted messages survive. Lands between 04's phase A and phase B; explicitly *not* a durability feature. |
| 15 | [Send ownership](15-send-ownership.md) | `Mount.send(SendTarget)` runs stage→deliver→commit framework-side; `reply_to`/`respond_to` adapters own the discord.py kwargs, hosts keep destination policy. Amends 01 §6 ("bind is the commit point"); before 09. |

## Relation to existing plans

`docs/plans/squid-layouts-migration.md` remains the tracker for deleting superseded
discord.py view classes. Its `primitives.presets` bullet is superseded by plan 04 here;
its "core design debts closed" list still describes the landed state this series builds
on.
