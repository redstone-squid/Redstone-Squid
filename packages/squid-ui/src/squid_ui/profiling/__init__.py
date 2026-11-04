"""Measure UI operations and inspect recent traces or aggregate timings.

A trace records one operation, such as dispatching a press. Spans time steps within
that operation, such as planning or delivery. MemoryProfiler retains selected completed
traces and timing summaries; snapshot returns immutable values for inspection or JSON
export. NoOpProfiler accepts the same instrumentation when profiling is disabled.
"""

from squid_ui.profiling.export import snapshot_json
from squid_ui.profiling.model import (
    ActionStatus,
    ActiveSpanSnapshot,
    ActiveTraceSnapshot,
    AggregateKey,
    AttributeValue,
    CounterAggregate,
    CounterAggregateKey,
    DispatchDisposition,
    DispatchResult,
    GenerationDecision,
    HistogramSnapshot,
    OperationAggregate,
    OperationKind,
    PresentationStatus,
    ProfilerHealth,
    RuntimeSnapshot,
    RuntimeSpan,
    RuntimeTrace,
    SpanAggregate,
    SpanAggregateKey,
    SpanAttribute,
    SpanId,
    TraceCounter,
    TraceId,
    TraceLink,
    TraceResult,
    TraceStatus,
)
from squid_ui.profiling.profiler import (
    DetachedSpanRecorder,
    MemoryProfiler,
    NoOpProfiler,
    OperationRecorder,
    Profiler,
    SpanRecorder,
)

__all__ = [
    "ActionStatus",
    "ActiveSpanSnapshot",
    "ActiveTraceSnapshot",
    "AggregateKey",
    "AttributeValue",
    "CounterAggregate",
    "CounterAggregateKey",
    "DetachedSpanRecorder",
    "DispatchDisposition",
    "DispatchResult",
    "GenerationDecision",
    "HistogramSnapshot",
    "MemoryProfiler",
    "NoOpProfiler",
    "OperationAggregate",
    "OperationKind",
    "OperationRecorder",
    "PresentationStatus",
    "Profiler",
    "ProfilerHealth",
    "RuntimeSnapshot",
    "RuntimeSpan",
    "RuntimeTrace",
    "SpanAggregate",
    "SpanAggregateKey",
    "SpanAttribute",
    "SpanId",
    "SpanRecorder",
    "TraceCounter",
    "TraceId",
    "TraceLink",
    "TraceResult",
    "TraceStatus",
    "snapshot_json",
]
