# 55 — Derived values on a shared namespace

## Problem

[40](40-shared-state.md) gave a namespace cells: state several mounts share, published on the
bus so every reader re-reads. It did not say what a *derived* value on a namespace is, and the
answer turned out to be two different answers that the code had half of.

**`sl.computed` on a namespace already worked, by accident, and nothing said so.** Measured:

```python
class Prefs(sl.Shared[int]):
    first: str = sl.cell("Ada")
    last: str = sl.cell("Lovelace")
    unread: str = sl.cell("not looked at")

    @sl.computed
    def full(self) -> str:
        return f"{self.first} {self.last}"
```

```
followed: ['Prefs(1).first', 'Prefs(1).last']     # not `unread`
redrawn:  ['Grace Lovelace']
```

It works because `_Derived` carries no address and `Observation.addresses()` recurses into its
sources, so a mount follows the cells behind the computed. That is exactly right, and
[54](54-resource-chains.md) made the recursion structural rather than an `isinstance(_Derived)`
special case. But there was not one test for it, and every existing test puts the computed on a
*component* reading a namespace.

**`sl.resource` on a namespace was silently accepted and then crashed.**

```
class built fine; descriptor: Resource
AttributeError: 'Catalog' object has no attribute 'invalidate'
```

`__init_subclass__` rejects a descriptor that is `_State` but not `_SharedCell`.
`_ResourceDescriptor` is neither, so it passed the gate, bound a real `Resource`, and died at
the first `_owner.invalidate()`. That is badly out of line with its neighbours, which are
exemplary:

```
sl.state() on a Shared    -> Bad.x: a shared namespace declares sl.cell(), not sl.state()
sl.cell()  on a Component -> BadPanel: sl.cell() declares state on an sl.Shared namespace,
                             not on a component; use sl.state() for x
```

Three of the four combinations were deliberate. The fourth was an AttributeError from inside
the machinery.

Both of those messages are gone as of [56](56-one-declaration.md), which collapsed `sl.cell()`
into `sl.state()`: every difference between them turned out to be a property of the owner. The
contrast above is kept because it is what made the resource gap visible.

## Decision

Support it. A namespace resource is a coherent and useful thing -- *load once, every mount
holding the namespace sees it* -- and most of the machinery was already there. What was missing
is one distinction.

**A computed needs no address; a resource does.** A computed is a pure function of tracked
cells, so everything that can move it is already followed, and it contributes nothing of its
own. A resource is loaded: it can move without any of its sources moving, through a `reload`,
a `replace`, or a topic publish. So it has to be followable in its own right.

### `Resource` gains an address

`Resource.address`, mirroring `_Cell.address`: `None` for a component's resource, because
nothing but its own component can be looking at it, and `CellAddress(namespace, name)` for a
namespace's.

`Observation.addresses()` emits an addressed source **and still walks it**. Both routes are
real dependencies: the resource can be reloaded out of band, and it can be re-pended by a cell
its loader read. A mount reading `catalog.entries` follows `Catalog(1).entries` *and*
`Catalog(1).key`.

### Publishing is the namespace's invalidation

`_landed()` publishes the address wherever a value is installed -- `Ready`, `Failed`,
`replace` -- and deliberately not on a re-pend: publishing a re-pend wakes every follower to
look at a value still on its way, and the reload that follows publishes anyway.

`_notify()` replaces the bare `self._owner.invalidate()`. A component's resource invalidates its
component, the only thing looking at it. A namespace's has already published, and the namespace
renders nothing itself, so the publish *is* the notification. That is what the `AttributeError`
was really telling us: `invalidate` is the component-shaped half of a two-shaped idea.

### One hook, on the owner

`_ResourceDescriptor.__get__` asks the instance for `_resource_binding(name)`, which returns
the address and the callable to publish it with. A component does not have the hook; `Shared`
returns `CellAddress(self, name), self.bus.publish`. Asked once per instance, inside the branch
that builds and caches the binding.

Keeping the hook on the owner is what stops `resources.py` needing to know what a namespace is:
it never imports `shared`, and the dependency runs the other way.

### Declaration becomes deliberate

`__init_subclass__` now recognises `_ResourceDescriptor` and `_Computed` explicitly and runs
them through the same name check as cells, so `bus` and `scope` and every underscored name are
refused for all three. Resources are recorded in `_resources`.

## Not doing

- **Persisting a namespace resource.** It is loaded, so rehydration is a reload; there is
  nothing to write down. [90](90-deferred.md) and [40](40-shared-state.md) §3 stand.
- **A `_slots` entry, so `_state_changed` publishes it.** `_state_changed` reports what a
  *commit* changed, and a load is not a commit. The resource publishes for itself.
- **Renaming `CellAddress`.** It now names a cell or a resource -- a named slot on a namespace.
  Accepted wart, same shape as 47's: churn on a just-shipped type for a name whose dominant
  case is still a cell.
- **Publishing on a re-pend.** Above.

## Verification

`tests/test_shared_resources.py` (13). For computeds, the behaviour that had no coverage at
all: derivation, per-instance independence, and a mount following the cells behind it rather
than the computed. For resources: one load serves every mount holding the namespace; both
follow routes appear; an out-of-band `reload` redraws every mount; a write to a cell the loader
read reloads *once* for everyone; a `replace` publishes when its action commits and a
rolled-back one publishes nothing ([48](48-resource-writes.md), seen from the bus); two
namespaces hold separate resources with different addresses; a component's resource has no
address; and reserved names are refused for both new declaration forms.

## Status

Implemented 2026-08-23.
