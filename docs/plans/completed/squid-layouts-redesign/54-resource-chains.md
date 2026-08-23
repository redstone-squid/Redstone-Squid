# 54 — A resource is a trackable source

## Problem

`sl.computed` derives from whatever cells it read, because `_Cell.read` calls `track()` and
`_Derived.settle()` re-checks those versions ([41](41-reactivity-cells.md)). An `sl.resource`
derives from cells the same way: its loader's tracked reads become `Resource.sources`, and
`_recheck` re-pends it when one moves.

**A resource could not derive from another resource.** `Resource` is neither a `_Cell` nor a
`_Derived`, and reading its `.state` registered nothing. Measured, not read:

```
node.sources: {}                       # the read tracked nothing
build pending after publish: True
node pending after publish: False      # node serves card(v1) forever while build is v2
```

Silent staleness, which is the worst shape: nothing raises, and the panel just stops matching
the database.

So an async value derived from another async value had no way to say so, and the two had to be
fused into one loader returning a tuple. That is what `BuildInfoComponent` and
`BuildEditComponent` do with `(build, node)` ([47](47-topic-values.md) phase 3): the card is
`await for_build(build).render_node()`, a derivation of the build, swept into the build's own
resource because it could not be expressed as one. `sl.computed` would be the right tool if
`render_node` were synchronous.

## What the mount already did, and what it could not

The pass loop needs nothing: `Mount._settle_atomic` renders, settles the pending tier, and
`continue`s (`mount.py`, up to `_MAX_LOAD_PASSES = 16`), re-checking `pending` before `_draw`.

But it cannot *discover* a chain. `_pending_resources` reads `tree.resources`, which is what
the **render** touched. A resource reached only from another resource's loader is not in it,
so it never settles:

```
node loader, build state: Pending      # the mount never ran build's loader
final build.sources: {}
```

That is what decided the design. The dependency has to be settled by the loader that needs it,
not discovered by the frontend.

## Design

### 1. `await` a resource, inside a loader

```python
@sl.resource(delivery=sl.ResourceDelivery.ATOMIC)
async def node(self) -> Node:
    return await render_node(await self.build)
```

`Resource.__await__` settles the dependency if it is pending, tracks the read, and raises
whatever the dependency raised. `.value` stays synchronous and still raises for a pending
resource, because a render has nowhere to wait -- awaiting is the loader's version of the same
read. A render cannot await, which is the point: a render reads what has settled.

This is also what orders the chain. `_settle_resources` starts a whole tier at once, so a
dependent discovered by the *frontend* would race its own input; a dependent that awaits it
cannot.

**Tracking happens after settling, never before.** Recording the version a resource held while
still pending leaves the caller stale against the value it just waited for, and re-pends it the
moment anyone looks. That bug cost a settle loop that never quiesced.

### 2. Reading a resource tracks it

`Resource.state` calls `track()`, mirroring `_Cell.read`. Every public read (`value`,
`pending`) goes through it.

**The machinery must not.** `_load`, `_invalidate` and the shared-wait path read `_state`
directly. `_load`'s pending guard originally read `state`, which tracked the *pre-load* version
onto whoever was loading -- the same staleness as above, arrived at from the other direction.

Falling out for free: a computed can derive from a resource, because `_Derived.settle()` walks
its sources the same way.

### 3. A version on every transition, and the epoch with it

`Resource.version` moves on `Ready`, on `Failed`, on `replace`, **and on a re-pend**. Including
the re-pend is what lets an invalidation reach a dependent immediately rather than one load
later, and it is safe precisely because §1 serialises the chain.

Every move goes through `_moved()`, which bumps the version *and* `_EPOCH`. The epoch half is
not optional: `_Derived.settle()` short-circuits on `self._epoch == _EPOCH`, so without it a
computed reading a resource would never re-derive.

`Resource.settle()` re-checks before answering. That is what carries an invalidation *down* a
chain: asking `node` whether it moved re-checks `build`, which re-checks the topic. `_recheck`
is therefore re-entrant, and returns the version in hand rather than recursing.

### 4. `Observation.addresses()` walks any consumer

The walk special-cased `_Derived`. `_Derived` and `Resource` are both `_Consumer`s -- an object
with `sources` -- and the principle is identical: a reader that used the value depends on
everything read to produce it. So the branch is structural, and reaches a resource nested inside
another resource's sources. A render reading only `node` follows the topic `build` watched.

**This deletes [47](47-topic-values.md) phase 2's `resources` parameter.** It existed to hand
`addresses()` the resources a render touched, because the loader's reads were invisible. With
§2 the render's own read puts the resource in `observation.sources`. Phase 2's bridge turns out
to be a special case of resource tracking.

One deliberate narrowing: the follow set is what the render *read*, not every resource it
touched -- the rule shared cells already live by.

### 5. Cycle detection, generalised

`_Derived` had a `_running` flag raising "`X` reads itself". For a mutual cycle that is a lie:
`first` reads `second` reads `first`, and it reported "Cyclic.first reads itself".

One task-local stack (`_SETTLING`) now holds every node producing a value, computed and resource
alike, and `ReactiveCycleError` carries the whole ring:

```
cycle: Cyclic.first -> Cyclic.second -> Cyclic.first
```

Shared between the two kinds because a chain can run through both -- a resource whose loader
reads a computed that reads that resource is a real cycle, and neither guard alone could see it.
The ring is reported from the node's *first* appearance, so a caller that merely reached the
cycle is not named: `entry -> a -> b -> a` reports `a -> b -> a`.

Task-local, so two resources settled concurrently cannot be mistaken for each other's
dependency, and a diamond -- two dependents sharing one input -- is not a cycle. It is a path
check, not a visited set.

`Resource.value` consults the same stack: reading the value of a resource whose loader you are
inside is a cycle, not bad luck, so it raises the ring rather than "pending, not ready".

### 6. Renames

- `Resource._settle` → `_load`. The source protocol is structural on `settle()`, and a
  synchronous `settle()` beside an asynchronous `_settle()` differing by one underscore is worse
  than a rename. `_load` is what it does. The `resource_settle.atomic` / `.visible` span names
  keep their spelling: they name the tier, not the method.
- `Resource._name` → `_label`, matching `_Derived._label`, so the shared stack can name either.
- `_Derived`'s cycle `RuntimeError` becomes `ReactiveCycleError`. Still a `RuntimeError`
  subclass, so `except RuntimeError` keeps working; the message and type both changed.

## Not doing

- **Frontend-side chain discovery.** The mount would have to walk `sources` transitively and
  settle tiers in dependency order, and a dependent's first load would still fail before the
  graph existed. `await` puts the wait where the dependency is known.
- **A declared `depends=` between resources.** [41](41-reactivity-cells.md) settled it.
- **Topologically sorting tiers.** The pass loop already orders them, one tier per pass.
- **Unfusing the bot's `(build, node)` pairs.** Possible now, but that is 47's territory. This
  plan makes it expressible, not mandatory.

## Verification

`tests/test_resource_chains.py` (13): awaiting tracks and settles in one pass; a publish two
links up re-pends the dependent, whose next load sees the new value and never the old one; a
computed re-derives when a resource reloads; every transition moves the version; a chain settles
through a mount in one `send` with one draw and no paint pairing a new input with an old
derivation; independent resources still settle together; and five cycle cases -- self, mutual,
ring-without-run-up, through a computed, and a diamond that must *not* trip it.

`test_computed.py` and `test_mount.py`'s cycle assertions now assert the path. The mount one is
the mutual case that used to be misreported.

## Status

Implemented 2026-08-23.
