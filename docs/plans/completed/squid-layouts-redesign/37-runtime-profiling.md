# 37 — Runtime profiling

## Status

Implemented in August 2026. The bounded collector, mounted and routed instrumentation,
TopicBus/Reactor causal delivery, immutable snapshots, JSON export, and owner-only devtools
surface have shipped. The optional OpenTelemetry bridge remains deliberately deferred until a
host needs live vendor export; it is not required for in-process profiling.

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

## Trace model and causal context

Squid owns a deliberately small in-process tracing core. OpenTelemetry is an optional live
adapter, not the package's data model or a core dependency: its SDK does not provide Squid's
bounded immutable snapshots, active-operation inspection, domain outcomes, or tail retention.
The core does not implement OTLP, W3C transport propagation, batching, or vendor conventions.

Public traces are frozen flat values. A trace has a 128-bit ID, an operation kind, timestamps,
an operation result, a tuple of spans, and bounded causal links. A span has a 64-bit ID, an
optional parent span ID, timestamps, outcome, typed bounded attributes, and bounded links. Flat
spans preserve overlapping siblings and serialize more cleanly than a nested object graph.
Identifiers use the standard trace/span widths so an optional exporter can correlate them
without translating the public model.

The profiler keeps private mutable active recorders and freezes them only when an operation
finishes. A task-local `ContextVar` holds the current trace and span. Synchronous context
managers surround async work—the recorder itself performs no I/O—and child AnyIO tasks inherit
their structural parent while maintaining independent task-local span stacks. A shared mutable
stack on the trace is forbidden because concurrent resource loads would corrupt it.

Parentage and causality are distinct:

- a parent span represents structurally nested work in the same operation;
- a trace link represents work caused across a queue, coalescing boundary, process integration,
  or otherwise independent operation.

TopicBus and Reactor capture the current lightweight `TraceLink` when scheduling. Their pending
state retains the first and latest trigger times, total trigger count, and only a bounded number
of links. A coalesced refresh can therefore link to several dispatches without pretending they
share one parent or retaining any live trace object.

Framework instrumentation uses typed attribute values rather than arbitrary dictionaries.
An optional public custom-span seam may accept bounded scalar attributes, with strict limits on
count and string length and no aggregation by arbitrary values.

## Dispatch disposition

Terminal disposition and generation handling are different dimensions. A rebased action can
complete, fail in its handler, or fail during delivery, so `REBASED` is not an outcome.

```python
class DispatchDisposition(StrEnum):
    MOUNT_FINISHED = "mount_finished"
    ACCESS_DENIED = "access_denied"
    ACCESS_FAILED = "access_failed"
    MISSING = "missing"
    INVALID_SELECTION = "invalid_selection"
    STALE = "stale"
    VALIDATION_RETRY = "validation_retry"
    COMPLETED = "completed"
    ACTION_FAILED = "action_failed"
    DELIVERY_FAILED = "delivery_failed"
    CANCELLED = "cancelled"


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

The terminal disposition is a convenient summary, not the only outcome. A dispatch result also
records independent action and presentation dimensions. Action outcomes distinguish not-run,
handled, middleware-short-circuited, failed, and cancelled. Presentation outcomes distinguish
not-required, acknowledged, no-change, written, abandoned, failed, and superseded. This preserves
cases such as a handler span failing, outer middleware recovering, and the overall dispatch
completing with a successful write. Delivery traces use the same presentation vocabulary.

The final implementation may use a boolean rather than the example `GenerationDecision`, but
must not make rebase a mutually exclusive terminal state.

## Public diagnostic values

The collector exposes frozen values only. Names are illustrative until implementation, but
the contract has three levels:

1. `RuntimeSnapshot`: cumulative and rolling-window aggregates, bounded active-operation
   snapshots, collector-health values, and recent traces retained by the configured collector.
2. `DispatchTrace` / `DeliveryTrace`: one completed operation, its disposition, generation
   decision, stable action or component identity, and a tuple of immutable spans.
3. `RuntimeSpan`: name, monotonic offset and duration, outcome, and bounded scalar attributes.

Snapshots never expose live tasks, locks, queues, middleware instances, interactions, mounts,
messages, handlers, or exceptions. Exceptions become bounded type/provenance strings; the
existing error hook remains responsible for presentation and error reporting.

Raw export serializes these public values rather than reaching back into the collector.

Active-operation snapshots expose only operation kind, stable provenance, current phase,
elapsed time, queue wait, and start time. They never expose the private mutable recorder. This
is essential for diagnosing work that is currently hung rather than only work that eventually
finished.

## Collection and retention

Profiling is explicitly configured and cheap when absent. The first version uses one injected
collector protocol with a no-op default; call sites do not branch into a global singleton.

The supplied in-memory collector has:

- separate fixed-size retention for ordinary samples, slow traces, failures, and acknowledgement
  deadline misses;
- cumulative and rolling counters for rebases, planner calls/cache hits/fallbacks/search work,
  coalesced triggers, and causal-link overflow; dispositions and failures remain aggregate keys;
- bounded histograms for both lifetime and rolling-window latency aggregation and percentiles;
- tail sampling after completion for successful high-volume operations, while failures, slow
  operations, and deadline misses are preferentially retained;
- an injected monotonic clock for deterministic tests.

Every operation contributes to counters and histograms even when its detailed trace is not
retained. Snapshot metadata includes observation counts, window boundaries, collector start/reset
epoch, schema version, configured bounds, buffer utilization, rejected attributes, dropped traces,
and collector failures. Monotonic values measure durations; an export also carries a wall-clock
anchor and process/boot identifier so traces can be aligned with logs and external incidents.

Aggregation keys are stable, low-cardinality identities such as action key, handler provenance,
component class, route format, subscriber label, and operation kind. Mount IDs, actor IDs,
message IDs, route parameter values, selected values, and arbitrary topics may appear in a
bounded recent trace when explicitly enabled, but never become unbounded aggregate labels.

Sensitive form values and interaction payloads are never recorded.

Profiling is observational and must never change framework behavior. Span closure runs in
`finally`; cancellation is recorded and re-raised; collector/exporter exceptions are contained;
and snapshot/export work never performs I/O on an instrumented hot path. The no-op profiler does
not generate IDs or read the clock. Benchmarks pin overhead for both disabled and enabled
collection.

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

For each coalesced refresh, trace time from the first trigger through settlement, record queue
wait from the first trigger and a `freshness` span from the latest trigger through settlement,
and retain trigger counts, bounded causal links, and link-overflow counters. This distinguishes
healthy batching from a projection that is continuously behind.

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

## Optional OpenTelemetry bridge

An integration may mirror spans into OpenTelemetry while they are live, allowing automatically
instrumented database and HTTP work to become children of Squid handler spans. Exporting only a
completed Squid trace is insufficient for that relationship. The bridge may adopt an existing
OpenTelemetry context as an external parent and correlate IDs, but Squid's memory collector and
devtools remain fully functional without it.

OpenTelemetry exporters, propagation, batching, and SDK lifecycle belong to the host and its
existing supervisor. The bridge is not part of the first implementation milestone.

## Landing order

1. Frozen trace/disposition/span values, task-local recorder, no-op and bounded-memory profilers,
   active snapshots, tail retention, rolling histograms, and stable JSON export.
2. Mount dispatch and acknowledgement instrumentation, including multidimensional outcomes and
   the generation-decision model.
3. Render/resource/planner/renderer/write spans shared by send, flush, refresh, and visible
   resource settlement.
4. Reactor and TopicBus snapshots, causal links, coalescing freshness, and queue/subscriber
   measurements.
5. Routed-action instrumentation.
6. Devtools aggregation and inspection.
7. Optional OpenTelemetry bridge, justified by concrete host demand. Framework owners already
   use the public operation/span recorder protocol; a second ambient custom-span API was not
   added without a concrete consumer.

Each stage remains useful without the later presentation stages and adds focused tests before
the next hot path is instrumented.

## Verification

- Injected-clock and ID-source unit tests pin span parentage, causal links, overlapping durations,
  acknowledgement source, cancellation, and total time.
- Dispatch tests cover every disposition and prove a rebase may complete, fail in the action, or
  fail in delivery without becoming a terminal `REBASED` outcome.
- Profiler tests prove active snapshots, all retention and histogram bounds, tail sampling,
  rolling windows, no-op behavior, immutable snapshots, and that sensitive values/high-cardinality
  IDs do not enter aggregate labels.
- Failure-injection tests prove profiler, collector, and export failures never alter the observed
  operation; microbenchmarks bound disabled and enabled recording overhead.
- Existing atomicity tests assert failed candidates are still rolled back and are reported as
  delivery failures without changing the live generation.
- Reactor tests cover queued/in-flight depth, coalescing, redelivery, cancellation, and failure.
- TopicBus tests cover subscriber latency/failure attribution without changing delivery order.
- Devtools tests cover empty profiles, low-sample percentile suppression, pagination, and JSON
  round-trip of the exported snapshot.
- Focused package tests with `--no-cov`, then `just typecheck` and `git diff --check`.
