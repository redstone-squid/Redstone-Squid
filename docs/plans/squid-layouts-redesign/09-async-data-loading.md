# 09 — Async data loading story

## Problem

`render()` being sync and pure is right. The consequence, with no framework support, is
that every stateful view hand-rolls `async def load(self)` that the *call site* must
remember to invoke before mounting: `squid/bot/settings.py:51`, `verify.py:77`,
`notifications.py:64`, `account_view.py` (x3), `notifications_view.py` (x2),
`settings_view.py:407`. Handlers like `SettingsPanel.open_voting` re-fetch and reassign
manually. `on_mount()` is sync, so the framework cannot own any of this. Forgetting
`load()` renders an empty panel with no error.

## Design

Give the mount an async boundary it already almost has, plus an opt-in resource
primitive. Two pieces, independently useful:

1. **Async `on_mount`.** Allow components to define `async def on_load(self) -> None`
   (new hook, distinct from sync `on_mount` to avoid changing existing semantics).
   `ComponentRuntime.commit` collects newly mounted components' `on_load` coroutines;
   the Discord mount awaits them before the first delivery (`send_component` path) and
   schedules them through the Reactor/refresh path when a component enters the tree
   mid-session. State written by `on_load` invalidates normally, producing the follow-up
   edit. The framework never spawns tasks (package rule): the awaiting happens inside
   the host-driven call (`send_component`, `dispatch` flush, `Reactor.run`).
2. **`sl.resource` descriptor** (opt-in sugar over the same hook):

       class Panel(sl.Component):
           weights: sl.Resource[tuple[RoleWeight, ...]] = sl.resource(lambda self: self._votes.get_role_weights(...))

   A `Resource` is declared state holding `pending | ready(value) | failed(error)`; the
   runtime schedules its fetch on mount and on explicit `.reload()`; `render()` branches
   on `resource.state` (the factory layer gains a `loading`/`failed` chrome helper).
   Fetch errors land in the resource, not the error hook, so a failed load renders as a
   degraded panel instead of vanishing.
3. **Host migration**: convert the hand-rolled `load()` implementations to `on_load`
   (mechanical rename plus deleting the call-site invocation); `open_voting`-style
   re-fetches become `resource.reload()` where the resource shape fits, or stay explicit
   where it does not. No flag-day: `load()` callers keep working until each file
   migrates.

Sequencing note: independent of plans 01–07; needs plan 01's stage/commit split landed
first only because "await loads before first delivery" belongs in the same
stage→deliver→commit seam.

## Verification

- `test_mount.py`: `on_load` runs before first delivery; a component embedded
  mid-session gets its `on_load` before the edit containing it; `on_load` state writes
  coalesce into one edit; a raising `on_load` routes to the error hook without wedging
  the mount.
- New `test_resources.py` for the descriptor's three states and `reload()`.
- Migrated host files' unit modules under `tests/unit/bot`, `--no-cov`.
