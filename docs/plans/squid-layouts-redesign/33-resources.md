# 30 — Reactive async resources

## Problem

`on_load` covers data a component cannot render without, but it is deliberately one-shot.
Async data that varies with component state still needs a hand-written state field, reload
method, dependency ordering, error branch, and stale-completion token. `WindowLoader` and
`SourceRankedList` are the worked example; `SettingsPanel` repeats the same shape for voting
configuration.

The original `sl.resource` sketch was deferred because it had no dependency model and its
`pending | ready | failed` states could not be observed under pre-render `on_load`. The runtime
now has descriptor-owned state, staged delivery, render-time component discovery, and the
request-ordering precedent in `WindowLoader`. Those pieces make the missing contract explicit.

## Design

### 1. One state machine, two delivery policies

```python
class VotingPanel(sl.Component):
    kind: VoteKind = sl.state(VoteKind.BUILD)

    @sl.resource(depends=(kind,))
    async def configuration(self) -> VoteConfiguration:
        return await self.votes.configuration(self.guild_id, self.kind)

    def render(self) -> sl.LayoutNode:
        match self.configuration.state:
            case sl.Pending(previous=None):
                return loading_panel()
            case sl.Pending(previous=sl.Ready(value=config)):
                return refreshing_panel(config)
            case sl.Failed(error=error, previous=previous):
                return failed_panel(error, previous)
            case sl.Ready(value=config):
                return voting_panel(config)
```

`ResourceState[T]` is `Pending[T] | Ready[T] | Failed[T]`. `Pending` and `Failed` retain
the previous `Ready[T]`, when one exists, so an author can keep stale content visible without
confusing it with fresh data. Loader exceptions become `Failed`; cancellation still propagates.
Resource state is runtime-only and is never exported in component snapshots.

`ResourceDelivery.VISIBLE` is the default. The mount commits a render containing `Pending`,
awaits every pending resource observed by that render, then commits the settled render.
`ResourceDelivery.ATOMIC` uses the same state machine but does not deliver its pending render:
it awaits the observed resource and stages again. Thus awaited loading is a delivery policy,
not a second component rendering model.

```python
@sl.resource(depends=(kind,), delivery=sl.ResourceDelivery.ATOMIC)
async def configuration(self) -> VoteConfiguration: ...
```

No resource task outlives `Mount.send`, `flush`, or `refresh_now`. Sibling loads use one anyio
task group, so task ownership and cancellation remain host-driven. A visible load may therefore
produce two Discord writes, but it creates no hidden background worker.

### 2. Explicit state dependencies

`depends=(kind,)` accepts the class-body references returned by `sl.state()`. Python sees the
actual descriptor at runtime even when the annotation presents `kind` as `VoteKind` to a type
checker. `Component.__init_subclass__` validates that every dependency is a state descriptor
owned by that component and builds the reverse map.

A committed dependency write changes the resource synchronously to `Pending(previous=...)`.
Assignments and observed in-place list/dict/set mutations both count; rolled-back writes do not.
Plain attributes cannot be dependencies. State fields must be declared before resources that
refer to them. Dependencies are exact fields rather than tracked loader reads, keeping fetches
free of ambient magic and making invalidation reviewable at the declaration.

Resources with no dependencies load once unless explicitly invalidated. A hidden resource is
lazy: only descriptor accesses made while rendering enter the component tree's observed resource
set, so changing a dependency does not fetch data for a branch the current render does not use.

### 3. Bound resource API and ordering

`@sl.resource(...)` binds a per-component `Resource[T]` with:

- `state: ResourceState[T]` and `value: T` (`value` raises unless the state is `Ready`);
- `invalidate()` to request a fresh value while retaining the last ready value;
- `async reload() -> ResourceState[T]` to settle immediately under the caller's task;
- `replace(value)` for an optimistic or otherwise authoritative local result.

Each invalidation advances a monotonic request token. A completion publishes only when its token
and dependency generation are still current; an older completion returns without changing the
newer pending state. Concurrent callers share an in-flight load for the same generation rather
than duplicating the fetch.

`replace` snapshots the current dependency generation, advances the token, installs `Ready`, and
invalidates the component. This is the optimistic-set half the original `SettingsPanel` analysis
required: a successful mutation can publish the authoritative result without an immediate read.

### 4. Render and mount integration

Resource descriptor access records the bound resource only while `render_component_tree` is
expanding its owner. `ComponentTree` carries the ordered, identity-deduplicated observations.
Portable rendering remains synchronous and simply exposes the current state; only an async
frontend chooses to settle it.

Discord staging first preserves existing component `on_load` behavior, then renders to discover
resources. It settles observed atomic resources before any delivery. Once a candidate is
committed, it settles observed visible resources and delivers the resulting candidate through
the newly committed edit handle. Newly revealed resources repeat the process under the existing
bounded load-pass guard.

If the first visible delivery yields no writable handle, the mount leaves the resource pending
and dirty; the next interaction or refresh settles it. A failed second delivery keeps the
already-committed pending generation live and the mount repairable, following the existing
candidate atomicity rules.

## Migration and verification

- Replace `SourceRankedList.loaded` plus its hand-written `on_load`/`_publish` ordering with one
  resource. Navigation invalidates or explicitly reloads it while preserving source positions,
  stale-window suppression, and capability-driven chrome.
- Cover state matching, previous-ready retention, explicit invalidation, optimistic replacement,
  dependency commits and rollbacks, in-place dependency mutation, stale completion, and loader
  cancellation in runtime tests.
- Cover visible pending then settled delivery, atomic single delivery, sibling concurrency,
  hidden-resource laziness, failed-state rendering, nested resources revealed after settlement,
  stale/failed second writes, and interaction acknowledgement in mount tests.
- Keep `on_load` as the lower-level hook for imperative component initialization and for
  non-resource work; it is neither deprecated nor implemented in terms of resources.

## Status

Approved for implementation 2026-08-22.
