# 72 — Incremental render caching

## Why

The current plan cache skips layout search and measurement on a structural hit, but a mounted
refresh still renders every component, hashes the complete document, lowers semantics, recollects
bindings, constructs a discord.py view and audits it before redundant-edit suppression can discover
that nothing visible changed. Profiling also shows repeated package-version verification and
cache-key construction costing more than the solver work the cache already removed.

The intended hot path is stricter:

- an unchanged scheduled refresh does no component, planner, renderer or Discord-object work;
- changing one component visits that component and its ancestor path, not its siblings;
- a changed document runs the global fitter only when it can affect a shared constraint or choice;
- every cache remains discardable: eviction changes work, never behaviour.

## Runtime snapshots

`Component.render()` is pure and synchronous, so the runtime memoizes it automatically. Each
component owns one current render snapshot containing its raw result, direct reactive dependencies,
injected-context tokens, provided values and async bindings. A second snapshot holds the expanded
and namespaced subtree, letting a valid subtree splice without walking or recollecting it.

Reactive observation becomes component-attributed. State, computed, resource, context and shared
topic changes invalidate only their recorded consumers. Computed backdating remains significant:
when a source changes but the computed value does not, the component snapshot stays valid. Explicit
`Component.invalidate()` invalidates that component. External `Mount.invalidate()`, `schedule()` and
direct external refreshes remain force boundaries for inputs the graph cannot name.

Context becomes a persistent versioned frame. `inject()` records the binding token it saw and
`provide()` records the final child-frame value, so a cache hit replays context without user code.

Lifecycle remains commit-scoped. Failed, deferred, raising and atomic-pending renders publish no
partial snapshot; removed components are pruned after commit; `finish()` clears all snapshots. A
render that constructs a component instance is not memoized, preserving the existing observable
replacement lifecycle of inline children.

## Planner reuse

Planning has three reuse lanes:

1. **Exact** returns the runtime-local current `PlanResult` when the document, target, presentation
   projection and other planner inputs are unchanged.
2. **Structural** executes a callback-free `PlanProgram` from `PlanCache`. It contains the scene,
   decisions, report, staged session writes and binding/resource slots, so current callbacks are
   materialized without lowering or measurement.
3. **Incremental** recompiles changed regions and reuses realized regions when a cached certificate
   proves the preferred lossless result still fits.

Framework-generated interaction behavior is represented by named, typed adapter descriptors rather
than lowering-local closures. A program stores their descriptor shape and dynamic slots; it never
stores Python bytecode, closure cells, authored callbacks or presentation sessions.

The incremental certificate is conservative. Updated resource totals and ancestor-local caps must
fit, and neither the affected island nor its boundary may contain pagination, shared `Budget`
allocation, a variant/fallback/strategy choice, lossy overflow, or an opaque extension. V2 children
compose directly. Classic loose prose is one maximal folding island, so changes recompute that run
and its immediate boundary.

A plain 2,000-character document growing to 2,500 characters under headroom therefore performs one
local replan and no global fit. Crossing a capacity or touching a coupled construct falls back to
the existing full planner. Differential tests require incremental and cold results to be identical.

Target fingerprints, adapter version parsing, package lookup, presentation revisions and document
identities are computed once at their immutable boundary. The canonical scene fingerprint and wire
format do not change. `PlanMetrics.reuse` distinguishes `miss`, `exact`, `structural` and
`incremental`; `cache_hit` stays compatible.

## Discord preflight and renderer programs

A mount stages subscriptions and plans before issuing a generation. It compares scene identity,
attachments and binding-key shape with the live generation. If all match, it commits the runtime,
callbacks, forms, subscriptions and presentation writes without constructing a renderer, view or
item. The visible generation stays live, `PresentationStatus.UNCHANGED` and `on_committed` still
fire, and `on_presented` does not.

A bounded `RenderProgramCache` stores callback-free constructor instructions and a structural audit
certificate keyed by scene, target and renderer configuration. Execution always allocates fresh
discord.py views, items, embeds and attachment wrappers. Custom wiring, factories and opaque
extensions retain final conformance checks.

`PlanCache` and `RenderProgramCache` default to 32 per runtime. Passing the same explicit instances
to multiple mounts opts into process-wide structural sharing. Shared entries never contain
callbacks, components, live resources or Discord objects, though they retain text until eviction.

## Verification

- Component tests cover local/shared state, computed backdating, resources, context, discovery,
  atomic pending, inline children, lifecycle, rollback, pruning and finish.
- Planner differential tests cover both targets, every reuse lane, current callback rebinding,
  forms, navigation, assets, locale/palette/session changes, capacity crossings and LRU eviction.
- Mount work-invariant tests prove an unchanged refresh performs zero component renders, lowering,
  global fits, renderer construction, Discord allocations, audits, edits and generation changes.
- A one-leaf update in a 100-component tree renders one component and no sibling.
- Renderer tests prove revisited scenes hit the program cache but return distinct mutable objects.
- A deterministic 1/100/1,000-component benchmark gates the mocked 100-component unchanged p95 at
  at most 25% of cold p95 and 2 ms; the existing warm-plan ceiling remains 10 ms.

Implementation lands as reviewable runtime, planner, mount, renderer and diagnostics commits, with
focused tests at each boundary followed by Pyrefly, architecture tests and the full unit suite.
