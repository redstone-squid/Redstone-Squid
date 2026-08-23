# 42 — Redundant edits: delivering a render the reader already has

## Problem

Nothing in the delivery path asks whether a staged render differs from the one on screen.
`Mount._write` (`discord/mount.py`) takes a presentation and writes it. `refresh_now` stages
a candidate and delivers it unconditionally. A render that would produce exactly the panel
the reader is looking at costs a Discord edit anyway.

Measured on a mount with a reactor and a bus, one click whose handler writes a shared cell
the same panel renders:

```text
edit through the interaction : 1
edit through standing handle : 1     <- same visible panel
deferrals                    : 0
```

The first edit answers the click ([40](40-shared-state.md) §7). The second is the bus
delivering the address this mount published to itself, and it repaints what is already there.
Two mounts sharing a namespace make it three edits where two were needed; adding panels adds
one redundant edit each.

That is the cheapest case to reproduce, not the only one. The same shape appears wherever a
refresh is scheduled by an address rather than by a difference:

- a host topic whose subscriber re-reads and finds nothing this panel projects has changed —
  `squid/bot/topics.py`'s `follow_resource` re-fetches, then refreshes regardless;
- a mount following several addresses that one commit moves together, where only one of them
  reaches the rendered tree;
- any `Reactor` delivery racing a flush that already showed the same state.

### Why this is not just wasted quota

`Mount._commit` sets `self._generation = candidate.generation`, and `_WiredButton` builds
`custom_id=_custom_id(mount.id, generation, key)`. Every delivered render therefore rotates
every control id and retires the previous generation, so a redundant edit is not inert: a
click already in flight against the old generation arrives against ids that no longer exist
and travels the rebase-or-stale path (`mount.py:1332-1428`). The cost is a race the reader
can lose, not only a request.

It also means "identical content" is false at the wire level even when it is true on screen.
The scene is a value — `SceneDocument` is a frozen dataclass — but a `DiscordPresentation`
holds a live `discord.ui.View` whose ids differ per generation, so two presentations of the
same panel never compare equal. Whatever answers this has to pick a layer to compare at, and
the obvious one is not the one that gets written to Discord.

### Why it has not bitten yet

Before [40](40-shared-state.md) a mount refreshed from a topic it did not itself publish, so
the re-read usually had found something. Shared cells made the mount its own publisher, which
is what turned an occasional redundancy into a per-click one. Nothing here is a regression —
`refresh_now` never compared — but the traffic it generates is new.

## What a design has to answer

Deliberately unanswered here. This file exists to hold the problem while
[40](40-shared-state.md) ships without it.

- At which layer is "the same panel" decided — scene, presentation, or the payload discord.py
  would send — and what does that layer fail to notice?
- Does suppressing an edit have to suppress the generation bump too, or is retiring control
  ids on an undelivered render harmless?
- What happens to a suppressed render's `session_updates`, assets and `_commit_presented`
  hooks, which today are the reward for having delivered?
- Is the answer at the mount at all, or upstream — a reactor that does not schedule, or a
  subscriber that does not publish?
- How is a suppression observed? A refresh that silently does nothing is indistinguishable
  from a broken subscription, which is the failure mode [40](40-shared-state.md) §7 spends a
  paragraph avoiding.

## Reproducing

A mount with a `Reactor` over a `TopicBus`, a component that renders one `sl.cell()` and
writes it from a handler, `fake_message()` and `fake_interaction()` from
`squid_layouts.discord.testing`: dispatch the press, then run the reactor over `bus.drain()`
and count `interaction.response.edit_message` against the message double's `edit`.

## Design

Decided 2026-08-23, answering the five questions above in order.

### 1. Compare at the scene, with two guards

`PlanReport.scene_fingerprint` (`planning/planner.py`) is already computed for every
render, and the scene is generation-free: control ids are minted at draw time by
`_WiredButton`/`_WiredSelect`, so the scene is the last layer at which "the same panel" is
even expressible. Comparing `DiscordPresentation` is impossible (a live view per generation),
and comparing discord.py's payload is the wrong altitude — it would rediscover the ids.

The scene misses two things, and each gets a guard in `Mount._same_as_live(candidate)`:

- **asset content** — `Asset.source` can change under the same name; the candidate's
  `assets` tuple must equal the live one by value, and unequal means deliver;
- **handler keys** — the retained controls must resolve the same logical keys. Binding values
  are runtime state rather than pixels, so a suppressed render publishes the candidate's
  handlers, policies, routes, guards, feedback, records and form bindings through the mount's
  existing key indirection.

`Status` is a scene node, so the reactor's paused-status edit is a genuine difference and
still delivers. A renewal screen ([39](39-ephemeral-handoff.md)) is a `_LifecycleCandidate`
and never reaches the comparison; `_same_as_live` also refuses any lifecycle but `ACTIVE`.

### 2. No generation bump on suppression

`Mount._commit` splits into `_commit_render` — `apply_updates`, `_prune_follows`,
`runtime.commit(tree, rendered_revision)`, handlers, form bindings, `_plan`, `_dirty`, `_pending` —
and `_commit_delivery` — `_generation`, `_assets`, the view swap, `live.track`.
`_commit` is the two in sequence; `_suppress` is `_commit_render` plus `candidate.view.stop()`.
The live generation keeps its control ids, so a click already in flight still matches and
never travels the rebase-or-stale path. `_issued` has already advanced for the staged
candidate, which is harmless: it only needs to be unique.

### 3. What a suppressed render earns

- `session_updates` **apply**: planning's clamps describe the scene on screen, and a
  suppressed candidate is on screen by definition.
- Assets are unchanged by construction (guard 1).
- `on_committed` hooks **do fire**, while `on_presented` hooks do not. Durability follows
  application runtime commits because hidden persistent state can advance without changing pixels.

### 4. At the mount, not upstream

Only the mount knows what the reader sees. The reactor cannot tell a self-publish from a
sibling's, the bus carries no origin, and [40](40-shared-state.md) §7 deliberately refused
a subscriber index. The check sits in the three paths that stage a render against an
existing one — `refresh_now`, `_flush` (an identical dirty render becomes acknowledge +
`UNCHANGED`; `_BusyPaint.restore()` already handles the non-written case) and
`_settle_visible` (a resource that loads to what the pending paint already showed) — and
never in `send`, which has nothing to compare against.

### 5. Observable, so silence is distinguishable from breakage

`PresentationOutcome.UNCHANGED` (distinct from `NO_CHANGE`, which means *not dirty*) is the
trace result; `refresh_now` now returns the outcome so the reactor can count it.
`ReactorSnapshot.unchanged`, `MountSnapshot.suppressed`, a `mount.suppressed` operation
counter, and one `logger.debug`. `/dev profile queues` prints `unchanged=`; `/dev ui inspect`
prints the mount's count. A dead subscription now looks different from a working one: no
REFRESH trace at all, versus a REFRESH trace ending `UNCHANGED`.

## Verification

- The reproduction above: after the self-write click, standing-handle edits are 0 and the
  interaction edit is 1; `ReactorSnapshot.unchanged == 1`.
- A click in flight against generation N still dispatches after a suppressed refresh.
- Status text, asset content, handler identity and any scene change all still deliver.
- A suppressed refresh requests a durability checkpoint without firing presentation observers.
- A visible resource that settles to the pending paint's value commits without a second edit.
- `tests/test_mount.py test_reactor.py test_shared_follow.py test_durable_runtime.py --no-cov`.

## Status

Implemented 2026-08-23 on `local-development`.
