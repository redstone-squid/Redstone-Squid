# 37 — Runtime profiling

## Status

Planned. This is the implementation boundary and data contract; no collector or devtools
surface has shipped yet.

## Problem

squid-layouts retains unusually good diagnostics for the generation currently on screen:
the committed scene, `PlanReport`, `PlanMetrics`, component and presentation state, handler
keys, access policy, generation, and message address. Those snapshots explain *what* the
mount committed, but not where an interaction or refresh spent its time, why work was
discarded, or whether a queue is falling behind.

The missing questions are operational:

- Did Discord receive an acknowledgement before its deadline, and did the watchdog send it?
- Was latency in admission, the action lock, middleware, the handler, resource settlement,
  component rendering, planning, Discord rendering, or the HTTP write?
- How often are stale generations rejected or rebased?
- Are refreshes being coalesced faster than the reactor can deliver them?
- Which TopicBus subscribers fail or dominate delivery time?
- Did a candidate commit, roll back after a failed write, or become obsolete before delivery?

The implementation must preserve the framework's existing immutable-snapshot philosophy.
It must not introduce a mutable global inspector, unbounded action history, high-cardinality
metric labels, or a second owner for background tasks.

## Shape: traces, counters, and snapshots

The delivery path is not a flat sequence. Acknowledgement races the handler, component and
atomic-resource loading may take several discovery passes, and visible resources may cause
several render/write cycles after the first generation commits. Represent one operation as a
small trace tree rather than forcing every duration into one row:

```text
dispatch
├── admission
│   ├── access
│   ├── action-lock wait
│   ├── generation policy
│   ├── form evaluation
│   └── guard
├── acknowledgement                    # may overlap execution
├── action
│   ├── middleware
│   └── handler
└── flush
    ├── component/resource load passes
    ├── runtime render
    ├── planning
    ├── Discord rendering
    ├── Discord write
    └── visible-resource passes
        └── render → plan → draw → write
```

Refreshes and sends reuse the render/delivery spans without inventing a dispatch. Reactor
and TopicBus operations have their own queue/delivery traces and cumulative counters.

Instrumentation records monotonic start and end values at the operation that owns the work.
Middleware cannot reconstruct planner, renderer, resource, or Discord-write timing and is
therefore not the profiling mechanism.

## Dispatch disposition

Terminal disposition and generation handling are different dimensions. A rebased action can
complete, fail in its handler, or fail during delivery, so `REBASED` is not an outcome.

```python
class DispatchDisposition(StrEnum):
    FINISHED = "finished"
    ACCESS_DENIED = "access_denied"
    ACCESS_FAILED = "access_failed"
    MISSING = "missing"
    INVALID_SELECTION = "invalid_selection"
    STALE = "stale"
    GUARD_DENIED = "guard_denied"
    VALIDATION_RETRY = "validation_retry"
    COMPLETED = "completed"
    ACTION_FAILED = "action_failed"
    DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True, slots=True)
class GenerationDecision:
    submitted: int | None
    active: int
    rebased: bool = False
```

`COMPLETED` means the admitted action chain and required presentation completed. A successful
handler followed by a failed Discord edit is `DELIVERY_FAILED`, not `COMPLETED`. A no-op action
that only requires acknowledgement is complete once acknowledgement succeeds. Rebase remains
metadata on all later dispositions.

The final implementation may use a boolean rather than the example `GenerationDecision`, but
must not make rebase a mutually exclusive terminal state.

## Public diagnostic values

The collector exposes frozen values only. Names are illustrative until implementation, but
the contract has three levels:

1. `RuntimeSnapshot`: cumulative counters, bounded aggregates, and snapshots of the recent
   traces retained by the configured collector.
2. `DispatchTrace` / `DeliveryTrace`: one completed operation, its disposition, generation
   decision, stable action or component identity, and a tuple of immutable spans.
3. `RuntimeSpan`: name, monotonic offset and duration, outcome, and bounded scalar attributes.

Snapshots never expose live tasks, locks, queues, middleware instances, interactions, mounts,
messages, handlers, or exceptions. Exceptions become type/provenance strings and a bounded
message suitable for owner-only diagnostics; the existing error hook remains responsible for
presentation and error reporting.

Raw export serializes these public values rather than reaching back into the collector.

## Collection and retention

Profiling is explicitly configured and cheap when absent. The first version uses one injected
collector protocol with a no-op default; call sites do not branch into a global singleton.

The supplied in-memory collector has:

- a fixed-size ring of recent completed traces;
- cumulative counters for dispositions, cache hits, coalescing, failures, and watchdog acks;
- bounded histograms for latency aggregation and percentiles;
- configurable sampling for successful high-volume operations, while failures and deadline
  misses may always be retained;
- an injected monotonic clock for deterministic tests.

Aggregation keys are stable, low-cardinality identities such as action key, handler provenance,
component class, route format, subscriber label, and operation kind. Mount IDs, actor IDs,
message IDs, route parameter values, selected values, and arbitrary topics may appear in a
bounded recent trace when explicitly enabled, but never become unbounded aggregate labels.

Sensitive form values and interaction payloads are never recorded.

## Instrumentation boundaries

### Mount

Record:

- access, binding resolution, action-lock wait, generation rejection/rebase, guard, middleware,
  handler, acknowledgement, flush, and total dispatch latency;
- form evaluation and validation retry;
- load/settlement pass count and duration by atomic versus visible delivery;
- runtime render, planner, Discord renderer, write, commit, and rollback;
- whether a flush was a no-op acknowledgement, used the interaction handle, fell back to the
  standing handle, or found no live authority;
- planner `cache_hit`, `states_explored`, and `search_fallback` from the committed candidate.

Acknowledgement latency is time from dispatch entry until Discord's initial response becomes
done. The trace separately records whether the handler/middleware, a write through the
interaction handle, the final no-op acknowledgement, or the watchdog satisfied it.

### Reactor

Add an immutable snapshot and traces/counters for queue depth, in-flight mounts, queue wait,
refresh duration/failure, coalesced schedules, and redelivery requested while in flight. The
existing per-mount sets remain the scheduling authority; profiling only observes them.

### TopicBus

Extend the existing `BusSnapshot` rather than replacing it. Add bounded subscriber duration
aggregates and record delivery failures by subscriber label. Preserve the payload-free bus and
its current per-topic sequential ordering. Arbitrary topic values do not become aggregate keys.

### Router

Routed middleware and handler timing use the same collector vocabulary where possible, but keep
`RouteRequest` and its Discord-specific dispatch model. Alias matching is metadata, just as mount
rebasing is metadata rather than a terminal result.

## Devtools

Extend the existing owner-only `DevTools` rather than create a second global inspector:

- `dev ui profile <mount>`: recent dispatch and delivery traces for one live mount;
- `dev profile actions`: action latency and disposition aggregates;
- `dev profile queues`: Reactor and TopicBus depth, latency, coalescing, and failures;
- `dev profile export`: attach the immutable runtime snapshot as JSON.

The mounted inspector may show a compact latest-operation summary, but full trace history gets
its own paged surface. Reading devtools takes a fresh snapshot on every render, as the existing
mount inspector does.

Percentiles are shown only when the retained histogram has enough observations and are labelled
with their sample count. A percentile over three calls is presentation noise, not a diagnosis.

## Ownership and cancellation

The collector starts no task in the initial implementation. Recording is synchronous aside from
the operation already being measured, and snapshot/export reads bounded in-memory structures.
If a later exporter needs background delivery, the host owns it through its existing anyio
supervisor; neither a mount nor middleware starts a bare task.

Cancellation is recorded as cancellation and re-raised. It is never converted into an ordinary
failure disposition or sent to an error hook.

## Landing order

1. Frozen trace/disposition/span values, collector protocol, no-op and bounded-memory collectors.
2. Mount dispatch and acknowledgement instrumentation, including the generation-decision model.
3. Render/resource/planner/renderer/write spans shared by send, flush, refresh, and visible
   resource settlement.
4. Reactor and TopicBus snapshots and queue/subscriber measurements.
5. Routed-action instrumentation.
6. Devtools aggregation, inspection, and JSON export.

Each stage remains useful without the later presentation stages and adds focused tests before
the next hot path is instrumented.

## Verification

- Injected-clock unit tests pin span nesting, durations, acknowledgement source, and total time.
- Dispatch tests cover every disposition and prove a rebase may complete, fail in the action, or
  fail in delivery without becoming a terminal `REBASED` outcome.
- Collector tests prove trace and histogram bounds, sampling, immutable snapshots, and that
  sensitive values/high-cardinality IDs do not enter aggregate labels.
- Existing atomicity tests assert failed candidates are still rolled back and are reported as
  delivery failures without changing the live generation.
- Reactor tests cover queued/in-flight depth, coalescing, redelivery, cancellation, and failure.
- TopicBus tests cover subscriber latency/failure attribution without changing delivery order.
- Devtools tests cover empty profiles, low-sample percentile suppression, pagination, and JSON
  round-trip of the exported snapshot.
- Focused package tests with `--no-cov`, then `just typecheck` and `git diff --check`.
