"""Frozen values describing profiled runtime operations."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OperationKind(StrEnum):
    """Framework operation represented by one trace."""

    DISPATCH = "dispatch"
    DELIVERY = "delivery"
    REFRESH = "refresh"
    SEND = "send"
    TOPIC_DELIVERY = "topic_delivery"
    REACTOR_DELIVERY = "reactor_delivery"
    ROUTE_DISPATCH = "route_dispatch"


class TraceOutcome(StrEnum):
    """Framework-independent terminal outcome for an operation or span."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class DispatchDisposition(StrEnum):
    """Terminal result of one mounted action dispatch."""

    MOUNT_FINISHED = "mount_finished"
    ACCESS_DENIED = "access_denied"
    ACCESS_FAILED = "access_failed"
    GUARD_DENIED = "guard_denied"
    GUARD_FAILED = "guard_failed"
    MISSING = "missing"
    INVALID_SELECTION = "invalid_selection"
    STALE = "stale"
    VALIDATION_RETRY = "validation_retry"
    COMPLETED = "completed"
    ACTION_FAILED = "action_failed"
    DELIVERY_FAILED = "delivery_failed"
    CANCELLED = "cancelled"


class ActionOutcome(StrEnum):
    """How far an admitted portable action chain progressed."""

    NOT_RUN = "not_run"
    HANDLED = "handled"
    SHORT_CIRCUITED = "short_circuited"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PresentationOutcome(StrEnum):
    """How the presentation required by an operation settled."""

    NOT_REQUIRED = "not_required"
    ACKNOWLEDGED = "acknowledged"
    NO_CHANGE = "no_change"
    UNCHANGED = "unchanged"
    """A dirty render was staged and found identical to the one on screen, so nothing was written."""
    WRITTEN = "written"
    ABANDONED = "abandoned"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class GenerationDecision:
    """Submitted and admitted render generations for one dispatch."""

    submitted: int | None
    active: int
    rebased: bool = False


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Independent terminal, action, presentation, and generation dispatch facts."""

    disposition: DispatchDisposition
    action: ActionOutcome
    presentation: PresentationOutcome
    generation: GenerationDecision


type AttributeValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class TraceId:
    """A W3C-compatible 128-bit trace identifier."""

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 16 or not any(self.value):
            message = "trace IDs must contain 16 bytes and cannot be all zero"
            raise ValueError(message)

    def __str__(self) -> str:
        return self.value.hex()


@dataclass(frozen=True, slots=True)
class SpanId:
    """A W3C-compatible 64-bit span identifier."""

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 8 or not any(self.value):
            message = "span IDs must contain 8 bytes and cannot be all zero"
            raise ValueError(message)

    def __str__(self) -> str:
        return self.value.hex()


@dataclass(frozen=True, slots=True)
class TraceLink:
    """A causal edge to work outside the current trace tree."""

    trace_id: TraceId
    span_id: SpanId


@dataclass(frozen=True, slots=True)
class SpanAttribute:
    """One bounded scalar attached to a retained span."""

    key: str
    value: AttributeValue


@dataclass(frozen=True, slots=True)
class TraceResult:
    """Framework-independent operation result and optional bounded detail."""

    outcome: TraceOutcome
    detail: str | None = None
    dispatch: DispatchResult | None = None
    presentation: PresentationOutcome | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSpan:
    """One completed interval within a runtime trace."""

    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    started: float
    duration: float
    outcome: TraceOutcome
    attributes: tuple[SpanAttribute, ...] = ()
    links: tuple[TraceLink, ...] = ()
    omitted_links: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeTrace:
    """One completed operation and its flat, parent-linked spans."""

    trace_id: TraceId
    root_span_id: SpanId
    operation: OperationKind
    name: str
    started: float
    duration: float
    result: TraceResult
    spans: tuple[RuntimeSpan, ...]
    links: tuple[TraceLink, ...] = ()
    omitted_links: int = 0
    deadline_missed: bool = False
    counters: tuple[TraceCounter, ...] = ()


@dataclass(frozen=True, slots=True)
class ActiveSpanSnapshot:
    """Safe diagnostic view of a span that has not finished."""

    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    started: float
    elapsed: float


@dataclass(frozen=True, slots=True)
class ActiveTraceSnapshot:
    """Safe diagnostic view of an operation that has not finished."""

    trace_id: TraceId
    operation: OperationKind
    name: str
    started: float
    elapsed: float
    current_spans: tuple[ActiveSpanSnapshot, ...]


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    """One fixed-bucket latency distribution."""

    bounds: tuple[float, ...]
    counts: tuple[int, ...]
    observations: int
    total: float
    minimum: float | None
    maximum: float | None

    def percentile(self, fraction: float) -> float | None:
        """Return the containing bucket's upper bound for ``fraction`` in ``[0, 1]``."""
        if not 0 <= fraction <= 1:
            message = "percentile fraction must be between zero and one"
            raise ValueError(message)
        if self.observations == 0:
            return None
        rank = max(1, math.ceil(fraction * self.observations))
        seen = 0
        for index, count in enumerate(self.counts):
            seen += count
            if seen >= rank:
                return self.maximum if index == len(self.bounds) else self.bounds[index]
        return self.maximum


@dataclass(frozen=True, slots=True)
class AggregateKey:
    """Bounded low-cardinality identity for an operation aggregate."""

    operation: OperationKind | None
    name: str
    outcome: TraceOutcome | None
    detail: str | None
    disposition: DispatchDisposition | None
    action: ActionOutcome | None
    presentation: PresentationOutcome | None


@dataclass(frozen=True, slots=True)
class OperationAggregate:
    """Lifetime and recent-window latency for one aggregate key."""

    key: AggregateKey
    lifetime: HistogramSnapshot
    window: HistogramSnapshot


@dataclass(frozen=True, slots=True)
class SpanAggregateKey:
    """Bounded low-cardinality identity for a span aggregate."""

    operation: OperationKind | None
    operation_name: str
    span_name: str
    outcome: TraceOutcome | None


@dataclass(frozen=True, slots=True)
class SpanAggregate:
    """Lifetime and recent-window latency for one span key."""

    key: SpanAggregateKey
    lifetime: HistogramSnapshot
    window: HistogramSnapshot


@dataclass(frozen=True, slots=True)
class TraceCounter:
    """One bounded counter contribution retained with a completed trace."""

    name: str
    value: int


@dataclass(frozen=True, slots=True)
class CounterAggregateKey:
    """Bounded low-cardinality identity for an operation counter."""

    operation: OperationKind | None
    operation_name: str
    counter_name: str


@dataclass(frozen=True, slots=True)
class CounterAggregate:
    """Lifetime and rolling-window sums for one operation counter."""

    key: CounterAggregateKey
    lifetime: int
    window: int


@dataclass(frozen=True, slots=True)
class ProfilerHealth:
    """Bounds and loss counters that make the profiler's own behavior visible."""

    active: int
    retained_recent: int
    retained_slow: int
    retained_failed: int
    retained_deadline_misses: int
    sampled_out: int
    dropped_traces: int
    evicted: int
    rejected_attributes: int
    internal_failures: int


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One immutable diagnostic view of an in-process profiler."""

    schema_version: int
    process_id: str
    started_at: datetime
    captured_at: datetime
    uptime: float
    window_seconds: float
    active: tuple[ActiveTraceSnapshot, ...]
    recent: tuple[RuntimeTrace, ...]
    slow: tuple[RuntimeTrace, ...]
    failed: tuple[RuntimeTrace, ...]
    deadline_misses: tuple[RuntimeTrace, ...]
    aggregates: tuple[OperationAggregate, ...]
    span_aggregates: tuple[SpanAggregate, ...]
    counter_aggregates: tuple[CounterAggregate, ...]
    health: ProfilerHealth
