# 57 — A render may not write the state it reads

## Problem

The rule is already stated as universal. `rendering()`'s docstring:

> A render produces a tree from state and must not change the state it is reading, so a shared
> write here would publish halfway through building the thing that reads it.

The first clause names every kind of state. The second names only shared state, and only the
second is enforced. Identical code, two outcomes:

```
torn:   drawn=['0/1']   pending=False   # component state
shared: ReactiveWriteError: SharedNs(1).n was written while a render was reading it
```

`Torn.render` reads `n` as `0`, writes `1`, reads it back, and draws `0/1` -- a tree no single
state ever produced. Nothing raises and nothing logs. Worse, `pending=False`: the write's
invalidation is swallowed, so the torn tree is not merely drawn, it is *final*.

[56](56-one-declaration.md) sharpened this. There used to be two spellings, and the difference
in render-time behaviour could at least be pinned on which one you wrote. Now there is one
`sl.state()` whose render-time behaviour depends invisibly on what holds it.

## Decision

`_State.__set__` refuses a write during a render whether or not the field is addressed.

The hazards differ in kind, so the messages do, and each names its own fix:

- **Addressed.** Keep today's message. The write is visible to other mounts mid-render, which
  is a correctness problem beyond this tree.
- **Unaddressed.** New message, naming the tear and pointing at the replacements below.

Both stay `ReactiveWriteError`.

### Construction is not mutation

The one real hazard, and the counter below hides it. A component built inside `render()` writes
its declared state while `rendering()` is true:

```python
class Parent(sl.Component):
    def render(self):
        return Child("hi")          # Child.__init__ assigns declared state
```

```
child built during render: True
```

That is a first-class pattern, and a naive guard breaks it.

The exemption already exists for the analogous case. `Component.__new__` calls
`_Transaction.note_born`, and `protects()` says why: "An object created during the action had
no state then, so writing to it is construction, not mutation." A render needs the same rule
and the same mechanism -- `observe_render()` grows a `born` set, `Component.__new__` notes into
whichever of the two is open, and the guard exempts an instance that is in it.

**Scope the exemption to construction, not to the whole render.** An instance born this render
should stop being exempt once its own `render()` runs; otherwise a child could tear its own
tree and be excused for it. If that turns out to need more plumbing than it is worth, the
fallback is exempting the instance for the whole render, which is what the transaction does --
but try the tighter rule first, and say in the code which one shipped.

## Blast radius, measured

`_State.__set__` instrumented to count writes where `rendering()` and the field has no address:

| suite | tests | component render-writes |
| --- | --- | --- |
| `packages/squid-layouts/tests` | 1485 | **0** |
| `tests/unit` | ~600 | **0** |

Zero, so this breaks nothing in the tree. There are no external users yet, so there is no
migration burden beyond the repo.

The count does **not** cover the construction case above, because no test in either suite
happens to build a component with declared state inside a render. That is why the exemption is
specified from the pattern rather than from the number.

## What replaces the patterns

Every reason to write during a render already has a better tool, and the new message should
name them:

| instead of | use | why it is not a render write |
| --- | --- | --- |
| lazy-init on first render | `sl.state(factory=...)` | evaluated per instance at first read, and cached |
| a value derived from state | `sl.computed` | recomputed when a source moves, never assigned |
| a value that must be fetched | `sl.resource`, or `on_load` | settled before the render that shows it |
| reacting to having been drawn | `on_mount` | runs after the tree reaches the frontend |

`factory=` is the important one, because lazy-init is the pattern people actually reach for.
Verified lazy rather than eager:

```
factory called at construction: False
first read: ['made'] -> calls: 1
second read: ['made'] -> calls: 1
```

## Not doing

- **A warning instead of an error.** The tear is silent and final today; a warning would be
  too, in any deployment that does not read logs. The whole point is that it stops.
- **Blocking writes to *undeclared* attributes during a render.** Different subject, already
  covered inside a transaction by `report_undeclared_write`.
- **Blocking reads that mutate a held value in place.** `opaque=` state can be mutated through
  its reference and no descriptor sees it. Real, and 41's replacement rule is the answer, not
  a render guard.

## Verification

- `Torn` raises, and the message names `factory=` and `sl.computed`.
- A shared write during a render keeps today's message, unchanged.
- **A component constructed inside a parent's `render()` still works**, assigning declared state
  in its `__init__` -- the test the count could not provide.
- A component born during a render that writes state in its own `render()` raises, if the
  tighter exemption ships.
- A write from an action handler, from `on_load` and from `on_mount` is unaffected.
- The package suite and `tests/unit` pass unchanged, which the measurement predicts.

## Status

Designed 2026-08-23. Not implemented.
