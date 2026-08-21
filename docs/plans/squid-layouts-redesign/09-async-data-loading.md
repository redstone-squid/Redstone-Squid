# 09 — `on_load`: async data before first delivery

## Problem

`render()` being sync and pure is right. The consequence, with no framework support, is
that every stateful view hand-rolls `async def load(self)` that the *call site* must
remember to invoke before mounting: `squid/bot/settings.py:51`, `verify.py:77`,
`notifications.py:64`, `account_view.py` (x3), `notifications_view.py` (x2),
`settings_view.py:407`. `on_mount()` is sync, so the framework cannot own any of this.
Forgetting `load()` renders an empty panel with no error. A component embedded
mid-session has it worse: no call site exists, so its data must arrive via constructor
or not at all.

## Design

One hook, awaited inside the seams plan 15 provides. The framework never spawns tasks
(package rule): all awaiting happens inside host-driven calls (`Mount.send`, `dispatch`
flush, `Reactor.run` → `refresh_now`).

1. **`async def on_load(self) -> None`** — new hook, distinct from sync `on_mount` so
   existing semantics do not change. Runs before the first delivery that would show the
   component, and before `render()` is ever called on it. At-most-once per instance on
   success; a raise leaves it eligible to retry on the next stage. A discarded candidate
   does *not* reset a completed load — its side effects happened.
2. **Where the awaiting happens.** `Mount.send(destination)` — the seam plan 15 landed,
   which already runs supersede → stage → deliver → commit. **As landed, discovery is
   folded into staging rather than run beside it**, because discovery *is* a render: a
   naive "stage, then load what it found" renders a component before its own `on_load`
   and brings the unloaded-state branches straight back. `render_component_tree` gained a
   `defer` predicate — an `Embed` whose component still owes a load is recorded in
   `ComponentTree.deferred` and not expanded — and `Mount._stage_loaded` loads a tier,
   renders to reveal the next, and draws only once nothing is deferred. A tier that still
   owes loads is rendered but never planned; a tree declaring no `on_load` renders and
   draws exactly once, as before. Siblings in a tier run under one
   `anyio.create_task_group()`. The `dispatch` flush and `refresh_now` paths do the same
   for components entering the tree mid-session, before the edit containing them.
   `finish`, `finish_via` and `build_view` deliberately load nothing.
3. **Budget rule, documented not enforced.** Loads triggered from an interaction share
   Discord's acknowledge window; a slow load belongs behind `event.acknowledge()`. No
   framework timeout constant, per plan 07's rule — deadlines the framework did not
   receive are not invented. In practice the dispatch path is covered anyway: `flush` runs
   inside `_invoke`'s watchdog task group, which defers the interaction at 2.5s. The
   `send` path has no watchdog, which is exactly today's exposure from an awaited
   `load()` preceding `mount.send`.
4. **Transactions (plan 08).** `on_load` never runs under a transaction; its writes are
   ordinary pre-delivery state, so plan 08's `__setattr__` tracking and PARALLEL_READ
   rejection do not apply to them. Stated in the docs table plan 08 adds.
5. **Errors.** A raising `on_load` raises before anything is staged, so there is no
   candidate to discard; it routes to the error funnel, no delivery happens and the mount
   stays re-sendable. **A lone failure is unwrapped out of anyio's exception group**:
   routing downstream of a mount is `isinstance`-based, so a `DomainError` arriving as an
   `ExceptionGroup` would get the generic crash card instead of its own wording. Several
   failures at once stay a group, for `except*`. The same unwrap was applied to `_invoke`'s
   watchdog group, which was already wrapping every delivery failure raised during
   dispatch.
   Authoring rule: data the panel cannot exist without → fetch in `on_load`; data it
   can degrade without → explicit state plus render branches, refreshed by handlers.
6. **Host migration.** The hand-rolled `load()` implementations rename to `on_load`;
   call sites delete the invocation (and, with plan 15, the surrounding ritual).
   `open_voting`-style handler re-fetches stay explicit methods — see below. Only three
   of the seven cited sites were live: the other four were on `ExpiringLayoutView`
   classes no production path reached, kept alive by their own tests. Those classes were
   deleted here rather than left to `squid-layouts-migration.md`, which uncovered a live
   defect — see Status.

**Cut from this plan: the `sl.resource` descriptor** (pending/ready/failed state with
`.reload()`), recorded in `90-deferred.md`. Two reasons. Under awaited loads, `pending`
is never observable at first paint, so the loading chrome it motivates has almost
nothing to show. And without a dependency model it makes the motivating file worse:
`settings_view.py:171` fetches `_preset`/`_weights` as a function of `self._kind`, and
`set_emojis` writes then re-fetches — a resource without declared deps and an
optimistic set turns that into `open_voting` renamed plus three render branches.
Revisit only with a dependency design.

Sequencing: after plan 15 — the pre-delivery await lives inside `Mount.send`. No other
dependencies.

## Verification

- `test_mount.py`: `on_load` completes before the target is called and the target
  receives the loaded render (exactly one delivery); two sibling loads run under one
  task group and their writes coalesce; a component embedded mid-session gets its
  `on_load` before the edit containing it; a raising `on_load` routes to the error
  funnel with no delivery, and the next `send` retries the load; a completed load does
  not re-run when the target raised and the send is retried.
- Plan 08 interaction: a load's plain-attribute writes are not transaction-tracked and
  do not trip PARALLEL_READ.
- Migrated host files' unit modules under `tests/unit/bot`, `--no-cov`.

## Status

Shipped.

Landed in five commits: the `defer` predicate and the hook; `_stage_loaded` and the seam
wiring; the three host panels; the deletion of the superseded view classes; docs.

The deletion fixed a live defect it had been hiding. `RoleWeightModal` and `VoteEmojiModal`
ended `on_submit` with `edit_interaction_layout(interaction, _panel_layout(self._panel))`, and
`_panel_layout` answered a `SettingsPanel` with `_compat_layout()` — the old `card_container`
rendering. So on the real `/settings` path, saving a role multiplier or an emoji preset
replaced the semantic message with the legacy card, drawn into a throwaway `LayoutView` the
mount knew nothing about. Plan 15 §5 migrated four modals to `mount.flush`; these two hid
behind that `isinstance` branch. Both now take the mount their launching event carries.

Cost accepted: the old classes' tests went with them without replacement, so `NotificationPanel`
and `AccountPanel` have no unit coverage of their own. Worth writing back against the
components.
