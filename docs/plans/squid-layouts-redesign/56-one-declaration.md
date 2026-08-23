# 56 — One `sl.state()`, wherever it is declared

## Problem

Declaring state took two spellings, and the package spent two error messages telling authors
which one they meant:

```
sl.state() on a Shared    -> Bad.x: a shared namespace declares sl.cell(), not sl.state()
sl.cell()  on a Component -> BadPanel: sl.cell() declares state on an sl.Shared namespace,
                             not on a component; use sl.state() for x
```

The messages were good. Needing them was the problem: **every difference between the two was a
property of the owner, not of the declaration.**

- `address()` returned `CellAddress` for a cell and `None` for state -- which is asking whether
  anyone outside the owner can see it, and a namespace is the thing that makes that true.
- `_SharedCell.__set__` refused a write during a render, where `_State` merely permitted it --
  again about visibility: a render writing state other mounts are reading has published a
  change halfway through building the thing that reads it.
- `cell()` hardcoded `persist=False` because a namespace is never persisted -- a fact about
  namespaces.

The author was being made to restate, at every field, something the class statement already
said. [40](40-shared-state.md) even said so out loud -- "`sl.cell()` is `sl.state()` one level
out and is literally the same storage" -- and `_SharedCell` was already a `_State` subclass
whose entire body was those two overrides.

## Decision

One `sl.state()`. `sl.cell()`, `_SharedCell`, and both error messages are deleted, because the
mistake they described stops being expressible.

The seam is an owner hook, the same shape [55](55-shared-derivations.md) used for resources:

```python
def address(self, instance: ReactiveOwner) -> Any:
    binding = getattr(instance, "_state_binding", None)
    return None if binding is None else binding(self.public_name)
```

`Shared._state_binding(name)` returns `CellAddress(self, name)`. A component has no hook and so
has no address. Asked of the *instance*, which matters for more than tidiness: a descriptor
declared on a mixin shared by a component and a namespace answers correctly for each, where a
flag set at class creation would have to pick one.

The render-write refusal moves to `_State.__set__`, applying exactly when the state is
addressed. The asymmetry is kept deliberately and now says why: a render writing its own
component is merely confused, while one writing shared state has published mid-render.

`persist=` needed one addition. It defaults to `not opaque`, so a namespace cannot simply
reject `persist=True` -- almost every field would trip it. `_State.persist_declared` records
whether the author asked, and `Shared.__init_subclass__` refuses only an explicit ask, with a
message that says why: a namespace's lifetime is whoever holds the handle.

## What this does not change

Nothing about behaviour. Shared state still publishes, component state still invalidates one
owner, and the storage was already identical. This is the declaration collapsing onto the
distinction that was doing the work.

## Not doing

- **Refusing a render-time write to component state.** Arguably right, and out of scope; it
  would be a behaviour change rather than a declaration one.
- **Keeping `sl.cell()` as an alias.** The point is that there is one way to say it.
- **Renaming `CellAddress`.** Still the accepted wart from [47](47-topic-values.md) and
  [55](55-shared-derivations.md).

## Verification

`tests/test_shared_state.py`: one declaration serves both owners; only a namespace gives its
state an address; an explicit `persist=True` on a namespace is refused while a field that
merely defaulted to it is fine. The two tests that pinned the deleted error messages are gone,
replaced by those.

A test bug surfaced while migrating and is worth recording: three multi-mount tests rebound
`mount` in a loop, so every mount but the last was garbage. A reactor holds its mounts weakly,
so they were testing whether the collector had run. It passed alone and failed after another
file created enough garbage. `mounted()` now hands the mount back and the tests keep it.

## Status

Implemented 2026-08-23. Supersedes 40's two-spelling surface and the note in
[55](55-shared-derivations.md) contrasting the two messages.
