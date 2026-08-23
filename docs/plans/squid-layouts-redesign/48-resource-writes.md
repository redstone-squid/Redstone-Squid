# 48 — A replaced resource value is a write

## Problem

Every write in the package stages. `sl.state` and `sl.cell` writes go into the transaction's
overlay ([41](41-reactivity-cells.md)): the action reads back what it wrote, nobody else sees
it until the commit lands, and a rollback is dropping the overlay. Subsystems that write
outside the cell graph have the `ActionParticipant` seam -- `prepare`/`apply`/`abort`/
`finalize` via `join_action` -- for exactly the same guarantees.

`Resource.replace` used neither. It set `self._state = Ready(value)` directly, so:

- a rolled-back action **kept** its replaced value, while every cell it wrote was restored;
- the value was visible to any other reader the moment `replace` returned, which is the dirty
  read [41](41-reactivity-cells.md) went to the overlay to avoid.

Confirmed by reproducer, not by reading:

```python
with pytest.raises(RuntimeError):
    with sl.transaction():
        panel.value.replace("edited")
        raise RuntimeError("handler failed")

assert panel.value.value == "loaded"   # failed: 'edited'
```

This blocked [47](47-topic-values.md) phase 3. `BuildEditComponent`'s `build` and `_node` are
`sl.state(opaque=True)` precisely so assignment rolls back, so moving them under a resource
would have silently traded that away.

## Decision

`replace` joins the action like any other participant. No new primitive: the seam already
existed and this is its second user.

- `replace` calls `join_action(self, ...)`. `None` means no transaction is open, and the
  value lands immediately as before.
- `_Replacement` holds the value, keyed by the resource, so repeated `replace` calls in one
  action collapse to the last -- exactly what repeated writes to a cell do.
- `apply` installs it through `_replace_now`, which is the old `replace` body verbatim,
  re-baselining sources and invalidating the owner. `abort` drops it. `prepare` cannot fail:
  the value is in hand and installing it cannot raise.
- `Resource.state` consults the staged replacement first and returns it as `Ready`, which is
  read-your-writes. It deliberately skips `_recheck` on that path: the action has declared
  this value authoritative, and `_recheck` would empty the `sources` that `apply` re-baselines
  -- dropping the watch a rolled-back action must leave intact.

`action_participant(key)` is new in `runtime/reactivity.py`: the read half of `join_action`,
for a subsystem answering "what did this action stage" on a path where staging would itself be
wrong. `join_action` runs the write guards on every call, so it cannot serve a read.

Falling out of the guards, and correct: `replace` during a render now raises
`ReactiveWriteError`, and a `PARALLEL_READ` action cannot replace. Both match `sl.state`.

## Not doing

- **Backing `_state` with a `_Cell`.** It would make staging automatic, but `_invalidate`,
  `_recheck` and `_settle` also write `_state`, and those are the loading machinery moving,
  not the application writing. `_recheck` in particular runs during a render, where a staged
  write raises. The distinction that matters is intent, not mechanism, and a participant
  expresses it exactly.
- **Deferring `_owner.invalidate()` to `finalize`.** `apply` already installed the value, and
  the owner is the only watcher; a second notification buys nothing.

## Verification

`tests/test_resource_transactions.py`: a rolled-back action keeps nothing; the action reads
back its own replacement; a reader outside the transaction sees the committed value; the last
replacement wins; replacing outside an action still lands immediately; and a rolled-back
replacement leaves the resource still watching its topic, so a later publish re-pends it.

## Status

Implemented 2026-08-23. Unblocks 47 phase 3.
