"""Frozen values describing profiled runtime operations."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OperationKind(StrEnum):
    """Which framework path a trace covers; also the default trace name when `Profiler.operation` gets none."""

    DISPATCH = "dispatch"
    DELIVERY = "delivery"
    REFRESH = "refresh"
    SEND = "send"
    TOPIC_DELIVERY = "topic_delivery"
    SCHEDULER_DELIVERY = "scheduler_delivery"
    ROUTE_DISPATCH = "route_dispatch"


class TraceStatus(StrEnum):
    """Terminal status of a trace or span; anything but `COMPLETED` sends the trace to the `failed` bucket."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """`asyncio.CancelledError` left the block, directly or inside an exception group."""
    ABANDONED = "abandoned"
    """A span still open when its trace closed, or a trace whose result was never set."""


class DispatchDisposition(StrEnum):
    """Why one mounted action dispatch ended; carried in `DispatchResult.disposition`."""

    MESSAGE_ROOT_FINISHED = "message_root_finished"
    ACCESS_DENIED = "access_denied"
    ACCESS_FAILED = "access_failed"
    """The access check itself raised, as opposed to answering no."""
    GUARD_DENIED = "guard_denied"
    GUARD_FAILED = "guard_failed"
    """The guard itself raised, as opposed to answering no."""
    CHALLENGE_ISSUED = "challenge_issued"
    """A guard asked the actor to reaffirm; nothing ran, and approval starts a fresh press."""
    CHALLENGE_DECLINED = "challenge_declined"
    """The actor answered a challenge with no, so the press it asked about never resumed."""
    MISSING = "missing"
    """No binding for the pressed key in the current render."""
    INVALID_SELECTION = "invalid_selection"
    STALE = "stale"
    """An `EXCLUSIVE` press from an older generation, or a `REBASE` press with nothing newer to rebase onto."""
    VALIDATION_RETRY = "validation_retry"
    """A form submission failed validation and the form was re-presented."""
    COMPLETED = "completed"
    ACTION_FAILED = "action_failed"
    DELIVERY_FAILED = "delivery_failed"
    CANCELLED = "cancelled"


class ActionStatus(StrEnum):
    """How far an admitted action's middleware chain got; carried in `DispatchResult.action`."""

    NOT_RUN = "not_run"
    HANDLED = "handled"
    SHORT_CIRCUITED = "short_circuited"
    """A middleware returned without calling `proceed`, so the handler never ran."""
    FAILED = "failed"
    CANCELLED = "cancelled"


class PresentationStatus(StrEnum):
    """What happened to the message the operation was expected to update."""

    NOT_REQUIRED = "not_required"
    ACKNOWLEDGED = "acknowledged"
    """The interaction was answered without editing the message."""
    NO_CHANGE = "no_change"
    UNCHANGED = "unchanged"
    """A dirty render was staged and found identical to the one on screen, so nothing was written."""
    WRITTEN = "written"
    ABANDONED = "abandoned"
    """No write happened and none will: the mount had finished, the destination declined, or the edit handle expired."""
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class GenerationDecision:
    """Which render generation a dispatch was admitted against."""

    submitted: int | None
    """Generation the actor pressed on; `None` when the press carries no generation (a challenge answer)."""
    active: int
    rebased: bool = False
    """`submitted` was older than `active` and the press was re-resolved against the newest render."""


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Four independent facts about one dispatch; each axis is aggregated separately."""

    disposition: DispatchDisposition
    action: ActionStatus
    presentation: PresentationStatus
    generation: GenerationDecision


type AttributeValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class TraceId:
    """16 random bytes, W3C trace-context shaped; `str()` is the 32-hex form.

    Raises `ValueError` for any other length or an all-zero value.
    """

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 16 or not any(self.value):
            message = "trace IDs must contain 16 bytes and cannot be all zero"
            raise ValueError(message)

    def __str__(self) -> str:
        return self.value.hex()


@dataclass(frozen=True, slots=True)
class SpanId:
    """8 random bytes, W3C trace-context shaped; `str()` is the 16-hex form.

    Raises `ValueError` for any other length or an all-zero value.
    """

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 8 or not any(self.value):
            message = "span IDs must contain 8 bytes and cannot be all zero"
            raise ValueError(message)

    def __str__(self) -> str:
        return self.value.hex()


@dataclass(frozen=True, slots=True)
class TraceLink:
    """A causal edge to a span in another trace, as `Profiler.capture_link` returns; kept up to `max_links` per node."""

    trace_id: TraceId
    span_id: SpanId


@dataclass(frozen=True, slots=True)
class SpanAttribute:
    """One retained attribute; strings are cut to 256 characters and the cut is counted as a rejection."""

    key: str
    value: AttributeValue


@dataclass(frozen=True, slots=True)
class TraceResult:
    """How an operation ended. `dispatch` is set only by action dispatches; `presentation` alone by deliveries."""

    status: TraceStatus
    detail: str | None = None
    """A qualified exception name on failure, or host text; cut to 240 characters."""
    dispatch: DispatchResult | None = None
    presentation: PresentationStatus | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSpan:
    """One finished span; the root span carries the trace's name, links and result status."""

    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    started: float
    """Seconds after the trace started."""
    duration: float
    status: TraceStatus
    attributes: tuple[SpanAttribute, ...] = ()
    links: tuple[TraceLink, ...] = ()
    omitted_links: int = 0
    """Links beyond `max_links` that were not kept."""


@dataclass(frozen=True, slots=True)
class RuntimeTrace:
    """One finished operation with its spans flat, root first, each pointing at its parent."""

    trace_id: TraceId
    root_span_id: SpanId
    operation: OperationKind
    name: str
    started: float
    """Seconds after the profiler started; add `RuntimeSnapshot.started_at` for wall time."""
    duration: float
    result: TraceResult
    spans: tuple[RuntimeSpan, ...]
    links: tuple[TraceLink, ...] = ()
    omitted_links: int = 0
    deadline_missed: bool = False
    """Set through `OperationRecorder.mark_deadline_missed`; selects the trace for that bucket."""
    counters: tuple[TraceCounter, ...] = ()


@dataclass(frozen=True, slots=True)
class ActiveSpanSnapshot:
    """A span still open at snapshot time; the root span is not listed."""

    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    started: float
    """Seconds after the trace started."""
    elapsed: float


@dataclass(frozen=True, slots=True)
class ActiveTraceSnapshot:
    """An operation still open at snapshot time; no result or counters exist yet."""

    trace_id: TraceId
    operation: OperationKind
    name: str
    started: float
    """Seconds after the profiler started."""
    elapsed: float
    current_spans: tuple[ActiveSpanSnapshot, ...]


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    """Fixed-bucket latency histogram; `counts` has one entry more than `bounds`, the last for values above them all."""

    bounds: tuple[float, ...]
    """Upper bounds in seconds, strictly increasing; a value counts in the first bucket whose bound it is within."""
    counts: tuple[int, ...]
    observations: int
    total: float
    minimum: float | None
    maximum: float | None

    def percentile(self, fraction: float) -> float | None:
        """Upper bound of the bucket holding the `fraction` rank, or `maximum` for the overflow bucket.

        Returns `None` with no observations. Raises `ValueError` when `fraction` is outside `[0, 1]`.
        """
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
    """Identity of an operation aggregate; the `<overflow>` key has every optional field `None`."""

    operation: OperationKind | None
    name: str
    status: TraceStatus | None
    detail: str | None
    disposition: DispatchDisposition | None
    action: ActionStatus | None
    presentation: PresentationStatus | None
    """`dispatch.presentation` when a dispatch result exists, else `TraceResult.presentation`."""


@dataclass(frozen=True, slots=True)
class OperationAggregate:
    """Latency for one `AggregateKey`; `window` covers the last `RuntimeSnapshot.window_seconds`."""

    key: AggregateKey
    lifetime: HistogramSnapshot
    window: HistogramSnapshot


@dataclass(frozen=True, slots=True)
class SpanAggregateKey:
    """Identity of a non-root span aggregate; the `<overflow>` key has both names set to `<overflow>`."""

    operation: OperationKind | None
    operation_name: str
    span_name: str
    status: TraceStatus | None


@dataclass(frozen=True, slots=True)
class SpanAggregate:
    """Latency for one `SpanAggregateKey`; `window` covers the last `RuntimeSnapshot.window_seconds`."""

    key: SpanAggregateKey
    lifetime: HistogramSnapshot
    window: HistogramSnapshot


@dataclass(frozen=True, slots=True)
class TraceCounter:
    """Final value of one `OperationRecorder.increment` counter on a finished trace."""

    name: str
    value: int


@dataclass(frozen=True, slots=True)
class CounterAggregateKey:
    """Identity of a counter aggregate; the `<overflow>` key has both names set to `<overflow>`."""

    operation: OperationKind | None
    operation_name: str
    counter_name: str


@dataclass(frozen=True, slots=True)
class CounterAggregate:
    """Sums for one `CounterAggregateKey`; `window` covers the last `RuntimeSnapshot.window_seconds`."""

    key: CounterAggregateKey
    lifetime: int
    window: int


@dataclass(frozen=True, slots=True)
class ProfilerHealth:
    """Occupancy and loss counters; a non-zero `internal_failures` means some traces or spans are missing."""

    active: int
    retained_recent: int
    retained_slow: int
    retained_failed: int
    retained_deadline_misses: int
    sampled_out: int
    """Ordinary traces `sample_rate` excluded from `recent`; still aggregated."""
    dropped_traces: int
    """Traces selected for retention that no bucket had room for (bucket size 0)."""
    evicted: int
    """Traces pushed out of a full bucket by a newer one."""
    rejected_attributes: int
    """Attributes dropped or cut for an invalid key, unsupported type, non-finite float, over-long string or count."""
    internal_failures: int
    """Exceptions the profiler swallowed rather than raise into instrumented code."""


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Frozen view of a profiler; `snapshot_json` serializes it."""

    schema_version: int
    process_id: str
    """Random per-profiler hex id; `"disabled"` from `NoOpProfiler`."""
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
