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
   component. At-most-once per instance on success; a raise leaves it eligible to retry
   on the next stage. A discarded candidate does *not* reset a completed load — its
   side effects happened.
2. **Where the awaiting happens.** `Mount.send(destination)` — the seam plan 15 landed,
   which already runs supersede → stage → deliver → commit: stage discovers components new
   to the tree; their `on_load`s run concurrently under one `anyio.create_task_group()`;
   state written by loads invalidates normally, so the mount stages again and the
   delivered view is the *loaded* render — loads coalesce into the first paint, no
   loading flash, one delivery. The `dispatch` flush and `refresh_now` paths do the
   same for components entering the tree mid-session, before the edit containing them.
3. **Budget rule, documented not enforced.** Loads triggered from an interaction share
   Discord's acknowledge window; a slow load belongs behind `event.acknowledge()`. No
   framework timeout constant, per plan 07's rule — deadlines the framework did not
   receive are not invented.
4. **Transactions (plan 08).** `on_load` never runs under a transaction; its writes are
   ordinary pre-delivery state, so plan 08's `__setattr__` tracking and PARALLEL_READ
   rejection do not apply to them. Stated in the docs table plan 08 adds.
5. **Errors.** A raising `on_load` aborts through the normal candidate-discard path and
   routes to the error funnel; no delivery happens and the mount stays re-sendable.
   Authoring rule: data the panel cannot exist without → fetch in `on_load`; data it
   can degrade without → explicit state plus render branches, refreshed by handlers.
6. **Host migration.** The hand-rolled `load()` implementations rename to `on_load`;
   call sites delete the invocation (and, with plan 15, the surrounding ritual).
   `open_voting`-style handler re-fetches stay explicit methods — see below.

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
