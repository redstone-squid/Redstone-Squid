# Dependency-based caching for squid-ui

Status: the four-step owner-local migration is implemented. Display templates are
separate from live bindings; `PlanningSnapshot` coordinates exact reuse and delivery
commit; the superseded memo and fingerprint implementation are removed; immutable
subtrees reuse their fingerprints. Cross-owner sharing and partial recertification
remain optional future work, not prerequisites for this design.

## How the pieces fit

Planning chooses and certifies a layout. Rendering turns its Scene into fresh SDK
objects. Delivery establishes what the remote message actually displays. These stages
have different dependencies and keep separate reuse mechanisms, coordinated by the
runtime rather than combined into one general-purpose cache.

| Owner | Retained value | What permits reuse |
|---|---|---|
| Component runtime | Expanded subtrees and reactive observations | Observed dependencies remain valid; lifecycle reconciliation still runs |
| `PlanningSnapshot` | Candidate and committed live plans | Same authored object, environment, presentation identity and revision |
| `RetainedComposition` | Certified allocation, prepared semantic graph, content fingerprints | Compatible authored structure, binding policy, environment and composition options |
| `ActionAllocation` | Bounded projections of display pages | Same certified page and current binding generation |
| `FingerprintCache` | Immutable subtree digests | The exact deeply immutable object is still present |
| `PlanCache` | Callback-free partition hints | Always recertified against current inputs |
| Renderer | Drawing instructions and audit status | Matching Scene and renderer/extension dependencies; each draw creates fresh SDK objects |
| Publication | Actual delivered Scene and control generation | Successful delivery or a verified unchanged presentation |

The runtime owns one `PlanningSnapshot`. Its committed result corresponds to the
presentation session's retained allocation. Keeping the latter on the session also
supports callers that use `plan()` without a runtime or snapshot.

## Display and live behavior

Built-in live allocations prepare `ActionPage` templates. A page contains a
`DisplayPage` (Scene, cost, effects and diagnostics), action slots and a page slot.
It contains no `PlanResult`, handlers, form schemas, file providers or presentation
updates. Navigation geometry retains neutral page ownership rather than owner callbacks.

The live `ActionAllocation` supplies current ordinary controls, region ownership and
`PageBindings` tables for forms, resources and unsupported actions. Projection resolves
the slots and applies frontend handler selection. Ordinary `ActionControl.on_trigger`
changes replace this live generation while keeping the certified templates. Grouped
controls retain scoped route mappings, including repeated local keys in different groups.
Guards, busy state, labels and history remain allocation dependencies.

Forms, native controls and actions inside alternative/fallback presentations use
conservative allocation comparison. Their live values are separated from display data,
but changes still require certification. Static plans and custom allocation selectors
keep their existing contracts. No artifacts are shared across owners automatically.

A retained diagnostic envelope has no obsolete page bindings. Holding only a built-in
display template therefore does not keep its form receiver or resource provider alive.
Holding a projected result intentionally keeps the bindings needed to dispatch it.

## Refresh and delivery

1. The runtime expands dirty components using reactive observations. Explicit refreshes
   still sample nonreactive application inputs through the component render.
2. `PlanningSnapshot` attempts exact replay before composition preparation. Its key
   captures the target, chrome, palette, localization, reservation, strictness,
   navigation/version, composition identity, search budget and a copy of positions.
   Source identity and presentation revision are checked separately. Route factories and opaque native extensions
   are never staged for exact replay because their external dependencies are unknown.
3. Otherwise, prepare the authored input and compare it with the retained allocation.
   Ordinary callback replacement and controlled page changes can reuse its certificate.
   Changed content, constraints, policies or environment require current certification.
   Previous partition hints may help search but never authorize an old certificate.
4. Select a certified page and project current bindings. Resolve cursors against current
   presentation state and stage their updates. A page revisit can reuse the projection;
   selection itself never commits a cursor or establishes delivery success.
5. Publication checks the actual delivered record. An unchanged presentation can skip
   drawing and editing while committing current bindings and reconciling lifecycle work.
   Changed output uses renderer instructions to construct fresh SDK objects and deliver.
6. After acceptance, publication applies session updates and commits the matching
   snapshot candidate before component commit hooks. Failed delivery leaves the
   previously displayed generation intact. A retry may reuse the pending computation,
   but must still attempt delivery. Superseded or unmatched results are not promoted.

Existing stale-candidate, authority, attachment and per-message partial-delivery rules
remain in publication. A planning cache hit grants no authority and cannot prove that
all continuation messages were delivered. There is no new all-or-nothing delivery rule.

## Incremental fingerprints and bounds

Content fingerprints use tagged, length-framed child digests. The cache keeps identity
and digest together, so Python object-ID recycling cannot produce a hit. A changed leaf
rehashes its ancestors while unchanged immutable subtrees contribute stored digests.
Hashes identify content; they do not authorize callback reuse or replace the allocation
comparer. The internal digest format is not the serialized Scene protocol.

Mutable mappings/sequences, opaque objects, callbacks and their ancestors are not
retained in the fingerprint cache. Immutable children can still hit. Fields omitted from
content identity also prevent caching their parent, since an omitted value could hold
live state. Cold and cached traversal produce the same digest.

Fingerprint retention is limited to 4,096 entries and 4 MiB of accounted subtree weight.
The accounting includes descendants and deliberately overcounts shared subtrees.
Page projections are limited to 32 entries and 1 MiB of accounted display/binding weight.
Oversized entries are returned without caching. Projection accounting charges serialized
Scene size and binding bookkeeping, not the application object graph behind borrowed
handlers or providers; it is not a process-memory limit. A new binding generation gets
an empty projection cache. Eviction never unmounts components or changes dispatch.
Closing the runtime clears its snapshot and fingerprint entries.

## Diagnostics and verification

`PlanMetrics.reuse` distinguishes exact, allocation and miss paths. Work and target
counts describe the current call, including zero work for exact replay.
`PlanningSnapshot.statistics()` reports exact hits, misses and the latest failure category
(source, environment, presentation or disabled). Fingerprint and projection snapshots
report hits, misses, entries, evictions and accounted bytes.

`PlanningSnapshot(enabled=False)` disables exact replay, retained allocation reuse,
partition hints and subtree hash reuse while preserving presentation history. It is the
cold reference path for tests; component and renderer caches have their own contracts.

Tests cover generated edits and page changes against that cold path under fixed
partitions; diagnostics, resource selection and committed cursors must agree. Additional
tests cover callback replacement, grouped dispatch, mutable coordinates, candidate versus
commit, weak-reference lifetimes, bounded eviction, failed binding and unchanged subtree
work counts. Existing publication tests exercise failed writes, stale work, lifecycle
changes and partial continuation delivery.

Run `uv run scripts/benchmarks/ui_planning_cache.py` for a repeatable planning-only
benchmark. One local run with 12 paragraphs, two fixed pages and an action gave these
medians over 60 updates (milliseconds):

| Scenario | Cold reference | Reuse enabled | Target evaluations across updates |
|---|---:|---:|---:|
| Cold start | 2.819 | 2.666 | 120 / 120 |
| Exact input | 2.978 | 0.010 | 120 / 0 |
| New callbacks | 3.346 | 0.759 | 120 / 0 |
| Page turn | 3.111 | 0.285 | 120 / 0 |
| One leaf edited | 2.967 | 2.583 | 120 / 120 |

The unchanged sibling subtrees reduce fingerprint work on leaf edits; certification
still runs. Timings are illustrative, not end-to-end latency guarantees. This run showed
no cold-start regression. Fingerprint accounting stayed below 190 KiB in these scenarios.

## What was removed, and what stays

`PlanMemo`, its accepted-revision set, the duplicate request/environment comparisons,
the old whole-tree JSON fingerprint path and transitional callback-rebinding wrappers
are removed. Callers pass `snapshot=PlanningSnapshot()` instead of `memo=PlanMemo()`.

Component observations, partition hints and renderer instruction caches remain because
they retain different results with different validity rules. Removing them would discard
useful work rather than simplify ownership. The next optional optimizations are explicit
cross-owner shareability and target-provided equivalence contracts for partial
recertification. Until those contracts exist, unknown dependencies cause recomputation.
