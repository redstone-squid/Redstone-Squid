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
