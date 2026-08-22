"""Task-local runtime tracing with bounded immutable retention."""

import asyncio
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from typing import Protocol, Self

from squid_layouts.profiling.model import (
    ActiveSpanSnapshot,
    ActiveTraceSnapshot,
    AggregateKey,
    AttributeValue,
    HistogramSnapshot,
    OperationAggregate,
    OperationKind,
    ProfilerHealth,
    RuntimeSnapshot,
    RuntimeSpan,
    RuntimeTrace,
    SpanAggregate,
    SpanAggregateKey,
    SpanAttribute,
    SpanId,
    TraceId,
    TraceLink,
    TraceOutcome,
    TraceResult,
)

_SCHEMA_VERSION = 1
_DEFAULT_BOUNDS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_MAX_NAME_LENGTH = 120
_MAX_DETAIL_LENGTH = 240
_MAX_ATTRIBUTE_KEY_LENGTH = 64
_MAX_ATTRIBUTE_STRING_LENGTH = 256
_MAX_ATTRIBUTES = 16
_OVERFLOW_AGGREGATE_NAME = "<overflow>"
_SYSTEM_RANDOM = secrets.SystemRandom()

type Clock = Callable[[], float]
type WallClock = Callable[[], datetime]
type IdSource = Callable[[int], bytes]
type SampleSource = Callable[[], float]


class SpanRecorder(Protocol):
    """Controls the outcome of the current span."""

    def set_outcome(self, outcome: TraceOutcome) -> None: ...

    def set_attribute(self, key: str, value: AttributeValue) -> None: ...


class DetachedSpanRecorder(SpanRecorder, Protocol):
    """A manually finished span for work that overlaps its lexical caller."""

    def finish(self, outcome: TraceOutcome = TraceOutcome.COMPLETED) -> None: ...


class OperationRecorder(Protocol):
    """Controls one operation and creates structurally nested spans."""

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[SpanRecorder]: ...

    def set_result(self, result: TraceResult) -> None: ...

    def mark_deadline_missed(self) -> None: ...

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> DetachedSpanRecorder: ...


class Profiler(Protocol):
    """Runtime tracing seam accepted by framework owners."""

    def operation(
        self,
        operation: OperationKind,
        *,
        name: str = "",
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[OperationRecorder]: ...

    def capture_link(self) -> TraceLink | None: ...

    def snapshot(self) -> RuntimeSnapshot: ...


@dataclass(slots=True)
class _MutableSpan:
    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    started: float
    attributes: tuple[SpanAttribute, ...]
    links: tuple[TraceLink, ...]
    omitted_links: int
    ended: float | None = None
    outcome: TraceOutcome | None = None


@dataclass(slots=True)
class _MutableTrace:
    trace_id: TraceId
    root_span_id: SpanId
    operation: OperationKind
    name: str
    started: float
    links: tuple[TraceLink, ...]
    omitted_links: int
    spans: dict[SpanId, _MutableSpan] = field(default_factory=dict)
    result: TraceResult | None = None
    deadline_missed: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class _Current:
    profiler: MemoryProfiler
    trace: _MutableTrace
    span_id: SpanId


_current: ContextVar[_Current | None] = ContextVar("squid_layouts_profile_span", default=None)


@dataclass(slots=True)
class _Histogram:
    bounds: tuple[float, ...]
    counts: list[int]
    observations: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    @classmethod
    def empty(cls, bounds: tuple[float, ...]) -> Self:
        return cls(bounds, [0] * (len(bounds) + 1))

    def observe(self, value: float) -> None:
        index = len(self.bounds)
        for candidate, bound in enumerate(self.bounds):
            if value <= bound:
                index = candidate
                break
        self.counts[index] += 1
        self.observations += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def merge(self, other: _Histogram) -> None:
        self.counts = [left + right for left, right in zip(self.counts, other.counts, strict=True)]
        self.observations += other.observations
        self.total += other.total
        if other.minimum is not None:
            self.minimum = other.minimum if self.minimum is None else min(self.minimum, other.minimum)
        if other.maximum is not None:
            self.maximum = other.maximum if self.maximum is None else max(self.maximum, other.maximum)

    def freeze(self) -> HistogramSnapshot:
        return HistogramSnapshot(
            self.bounds,
            tuple(self.counts),
            self.observations,
            self.total,
            self.minimum,
            self.maximum,
        )


@dataclass(slots=True)
class _WindowSlice:
    index: int
    histograms: dict[AggregateKey, _Histogram] = field(default_factory=dict)
    span_histograms: dict[SpanAggregateKey, _Histogram] = field(default_factory=dict)


class _NoOpSpan(AbstractContextManager[SpanRecorder]):
    def __enter__(self) -> SpanRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    def set_outcome(self, outcome: TraceOutcome) -> None:
        pass

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        pass


class _NoOpOperation(_NoOpSpan, OperationRecorder):
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[SpanRecorder]:
        return self

    def set_result(self, result: TraceResult) -> None:
        pass

    def mark_deadline_missed(self) -> None:
        pass

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> DetachedSpanRecorder:
        return _NOOP_DETACHED


class _NoOpDetachedSpan(_NoOpSpan, DetachedSpanRecorder):
    def finish(self, outcome: TraceOutcome = TraceOutcome.COMPLETED) -> None:
        pass


_NOOP_DETACHED = _NoOpDetachedSpan()
_NOOP = _NoOpOperation()


class NoOpProfiler:
    """Profiler implementation whose instrumented hot path performs no work."""

    def operation(
        self,
        operation: OperationKind,
        *,
        name: str = "",
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[OperationRecorder]:
        return _NOOP

    def capture_link(self) -> TraceLink | None:
        return None

    def snapshot(self) -> RuntimeSnapshot:
        epoch = datetime.fromtimestamp(0, UTC)
        return RuntimeSnapshot(
            _SCHEMA_VERSION,
            "disabled",
            epoch,
            epoch,
            0.0,
            0.0,
            (),
            (),
            (),
            (),
            (),
            (),
            (),
            ProfilerHealth(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        )


class _SpanScope(AbstractContextManager[SpanRecorder], SpanRecorder):
    def __init__(
        self,
        profiler: MemoryProfiler,
        trace: _MutableTrace,
        parent_span_id: SpanId,
        name: str,
        attributes: Mapping[str, AttributeValue] | None,
        links: Sequence[TraceLink],
    ) -> None:
        self._profiler = profiler
        self._trace = trace
        self._parent_span_id = parent_span_id
        self._name = name
        self._attributes = attributes
        self._links = links
        self._span: _MutableSpan | None = None
        self._token: Token[_Current | None] | None = None
        self._fallback = False

    def __enter__(self) -> SpanRecorder:
        try:
            self._span = self._profiler._start_span(
                self._trace,
                self._parent_span_id,
                self._name,
                self._attributes,
                self._links,
            )
            self._token = _current.set(_Current(self._profiler, self._trace, self._span.span_id))
        except BaseException:
            self._fallback = True
            self._profiler._note_internal_failure()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        if self._fallback or self._span is None:
            return
        self._reset_context()
        outcome = _outcome_for_exception(exc) if exc is not None else self._span.outcome or TraceOutcome.COMPLETED
        self._profiler._finish_span(self._trace, self._span, outcome)
        return

    def set_outcome(self, outcome: TraceOutcome) -> None:
        if self._span is not None:
            self._span.outcome = outcome

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        if self._span is not None:
            self._profiler._set_span_attribute(self._trace, self._span, key, value)

    def _reset_context(self) -> None:
        if self._token is None:
            return
        try:
            _current.reset(self._token)
        except BaseException:
            self._profiler._note_internal_failure()


class _OperationScope(AbstractContextManager[OperationRecorder], OperationRecorder):
    def __init__(
        self,
        profiler: MemoryProfiler,
        operation: OperationKind,
        name: str,
        attributes: Mapping[str, AttributeValue] | None,
        links: Sequence[TraceLink],
    ) -> None:
        self._profiler = profiler
        self._operation = operation
        self._name = name
        self._attributes = attributes
        self._links = links
        self._trace: _MutableTrace | None = None
        self._token: Token[_Current | None] | None = None
        self._fallback = False

    def __enter__(self) -> OperationRecorder:
        try:
            self._trace = self._profiler._start_trace(
                self._operation,
                self._name,
                self._attributes,
                self._links,
            )
            self._token = _current.set(_Current(self._profiler, self._trace, self._trace.root_span_id))
        except BaseException:
            self._fallback = True
            self._profiler._note_internal_failure()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        if self._fallback or self._trace is None:
            return

        self._reset_context()

        if exc is not None:
            outcome = _outcome_for_exception(exc)
            detail = None if outcome is not TraceOutcome.FAILED else _exception_name(exc)
            dispatch = None if self._trace.result is None else self._trace.result.dispatch
            presentation = None if self._trace.result is None else self._trace.result.presentation
            result = TraceResult(outcome, detail, dispatch, presentation)
        else:
            result = self._trace.result or TraceResult(TraceOutcome.COMPLETED)
        self._profiler._finish_trace(self._trace, result)
        return

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[SpanRecorder]:
        if self._trace is None or self._fallback or self._trace.closed:
            return _NOOP
        current = _current.get()
        parent = (
            current.span_id
            if current is not None and current.profiler is self._profiler and current.trace is self._trace
            else self._trace.root_span_id
        )
        return _SpanScope(self._profiler, self._trace, parent, name, attributes, links)

    def set_result(self, result: TraceResult) -> None:
        if self._trace is not None and not self._trace.closed:
            self._trace.result = result

    def mark_deadline_missed(self) -> None:
        if self._trace is not None and not self._trace.closed:
            self._trace.deadline_missed = True

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> DetachedSpanRecorder:
        if self._trace is None or self._fallback or self._trace.closed:
            return _NOOP_DETACHED
        current = _current.get()
        parent = (
            current.span_id
            if current is not None and current.profiler is self._profiler and current.trace is self._trace
            else self._trace.root_span_id
        )
        try:
            span = self._profiler._start_span(self._trace, parent, name, attributes, links)
        except BaseException:
            self._profiler._note_internal_failure()
            return _NOOP_DETACHED
        return _DetachedSpan(self._profiler, self._trace, span)

    def _reset_context(self) -> None:
        if self._token is None:
            return
        try:
            _current.reset(self._token)
        except BaseException:
            self._profiler._note_internal_failure()


class _DetachedSpan(DetachedSpanRecorder):
    def __init__(self, profiler: MemoryProfiler, trace: _MutableTrace, span: _MutableSpan) -> None:
        self._profiler = profiler
        self._trace = trace
        self._span = span
        self._finished = False

    def set_outcome(self, outcome: TraceOutcome) -> None:
        if not self._finished and not self._trace.closed:
            self._span.outcome = outcome

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        if not self._finished:
            self._profiler._set_span_attribute(self._trace, self._span, key, value)

    def finish(self, outcome: TraceOutcome = TraceOutcome.COMPLETED) -> None:
        if self._finished or self._trace.closed:
            return
        self._finished = True
        self._profiler._finish_span(self._trace, self._span, self._span.outcome or outcome)


def _outcome_for_exception(error: BaseException | None) -> TraceOutcome:
    if error is None:
        return TraceOutcome.COMPLETED
    if isinstance(error, asyncio.CancelledError):
        return TraceOutcome.CANCELLED
    if isinstance(error, BaseExceptionGroup) and any(
        _outcome_for_exception(nested) is TraceOutcome.CANCELLED for nested in error.exceptions
    ):
        return TraceOutcome.CANCELLED
    return TraceOutcome.FAILED


def _exception_name(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"[:_MAX_DETAIL_LENGTH]


class MemoryProfiler:
    """Bounded in-memory runtime profiler with active-operation snapshots and tail retention.

    Args:
        recent: Maximum sampled ordinary traces retained.
        slow: Maximum slow traces retained.
        failed: Maximum failed or cancelled traces retained.
        deadline_misses: Maximum acknowledgement deadline misses retained.
        slow_threshold: Duration in seconds at which a trace enters slow retention.
        sample_rate: Fraction of ordinary successful traces retained in ``recent``.
        window_seconds: Duration represented by rolling aggregate histograms.
        window_slices: Number of rotating histogram slices in the rolling window.
        max_aggregate_keys: Maximum lifetime aggregate keys before overflow grouping.
        max_span_aggregate_keys: Maximum lifetime span aggregate keys before overflow grouping.
        max_links: Maximum causal links retained on one trace or span.
        clock: Monotonic clock used for durations and rolling windows.
        wall_clock: UTC wall clock used only for snapshot/export correlation.
        id_source: Source of random identifier bytes.
        sample_source: Source of values in ``[0, 1)`` for deterministic sampling tests.
    """

    def __init__(
        self,
        *,
        recent: int = 128,
        slow: int = 64,
        failed: int = 64,
        deadline_misses: int = 32,
        slow_threshold: float = 1.0,
        sample_rate: float = 1.0,
        window_seconds: float = 300.0,
        window_slices: int = 6,
        max_aggregate_keys: int = 256,
        max_span_aggregate_keys: int = 512,
        max_links: int = 8,
        histogram_bounds: Sequence[float] = _DEFAULT_BOUNDS,
        clock: Clock = time.monotonic,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        id_source: IdSource = secrets.token_bytes,
        sample_source: SampleSource = _SYSTEM_RANDOM.random,
    ) -> None:
        sizes = (recent, slow, failed, deadline_misses, max_aggregate_keys, max_span_aggregate_keys, max_links)
        if any(size < 0 for size in sizes) or window_slices < 1:
            message = "profiler bounds cannot be negative and window_slices must be positive"
            raise ValueError(message)
        if slow_threshold < 0 or window_seconds <= 0:
            message = "profiler durations must be positive"
            raise ValueError(message)
        if not 0 <= sample_rate <= 1:
            message = "sample_rate must be between zero and one"
            raise ValueError(message)

        bounds = tuple(histogram_bounds)
        if any(not math.isfinite(bound) or bound <= 0 for bound in bounds) or any(
            left >= right for left, right in pairwise(bounds)
        ):
            message = "histogram bounds must be finite, positive, and strictly increasing"
            raise ValueError(message)

        self._clock = clock
        self._wall_clock = wall_clock
        self._id_source = id_source
        self._sample_source = sample_source
        self._slow_threshold = slow_threshold
        self._sample_rate = sample_rate
        self._window_seconds = window_seconds
        self._window_slices = window_slices
        self._slice_seconds = window_seconds / window_slices
        self._max_aggregate_keys = max_aggregate_keys
        self._max_span_aggregate_keys = max_span_aggregate_keys
        self._max_links = max_links
        self._bounds = bounds
        self._lock = threading.RLock()
        self._active: dict[TraceId, _MutableTrace] = {}
        self._recent: deque[RuntimeTrace] = deque(maxlen=recent)
        self._slow: deque[RuntimeTrace] = deque(maxlen=slow)
        self._failed: deque[RuntimeTrace] = deque(maxlen=failed)
        self._deadline_misses: deque[RuntimeTrace] = deque(maxlen=deadline_misses)
        self._lifetime: dict[AggregateKey, _Histogram] = {}
        self._span_lifetime: dict[SpanAggregateKey, _Histogram] = {}
        self._window: deque[_WindowSlice] = deque()
        self._started = clock()
        self._started_at = wall_clock()
        self._process_id = secrets.token_hex(8)
        self._overflow_key: AggregateKey | None = None
        self._span_overflow_key: SpanAggregateKey | None = None
        self._sampled_out = 0
        self._dropped_traces = 0
        self._evicted = 0
        self._rejected_attributes = 0
        self._internal_failures = 0

    def operation(
        self,
        operation: OperationKind,
        *,
        name: str = "",
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[TraceLink] = (),
    ) -> AbstractContextManager[OperationRecorder]:
        return _OperationScope(self, operation, name, attributes, links)

    def capture_link(self) -> TraceLink | None:
        current = _current.get()
        if current is None or current.profiler is not self or current.trace.closed:
            return None
        return TraceLink(current.trace.trace_id, current.span_id)

    def snapshot(self) -> RuntimeSnapshot:
        now = self._clock()
        captured_at = self._wall_clock()
        with self._lock:
            active = tuple(self._active_snapshot(trace, now) for trace in self._active.values())
            window = self._window_histograms(now)
            span_window = self._span_window_histograms()
            aggregates = tuple(
                OperationAggregate(
                    key,
                    histogram.freeze(),
                    window.get(key, _Histogram.empty(self._bounds)).freeze(),
                )
                for key, histogram in self._lifetime.items()
            )
            span_aggregates = tuple(
                SpanAggregate(
                    key,
                    histogram.freeze(),
                    span_window.get(key, _Histogram.empty(self._bounds)).freeze(),
                )
                for key, histogram in self._span_lifetime.items()
            )
            health = ProfilerHealth(
                len(active),
                len(self._recent),
                len(self._slow),
                len(self._failed),
                len(self._deadline_misses),
                self._sampled_out,
                self._dropped_traces,
                self._evicted,
                self._rejected_attributes,
                self._internal_failures,
            )
            return RuntimeSnapshot(
                _SCHEMA_VERSION,
                self._process_id,
                self._started_at,
                captured_at,
                max(0.0, now - self._started),
                self._window_seconds,
                active,
                tuple(self._recent),
                tuple(self._slow),
                tuple(self._failed),
                tuple(self._deadline_misses),
                aggregates,
                span_aggregates,
                health,
            )

    def _new_id(
        self,
        size: int,
        constructor: Callable[[bytes], TraceId | SpanId],
        used: set[TraceId] | set[SpanId] | None = None,
    ) -> TraceId | SpanId:
        for _ in range(3):
            value = self._id_source(size)
            if len(value) == size and any(value):
                identifier = constructor(value)
                if used is None or identifier not in used:
                    return identifier
        message = f"ID source did not produce a unique, non-zero {size}-byte ID"
        raise ValueError(message)

    def _start_trace(
        self,
        operation: OperationKind,
        name: str,
        attributes: Mapping[str, AttributeValue] | None,
        links: Sequence[TraceLink],
    ) -> _MutableTrace:
        started = self._clock()
        with self._lock:
            active_ids = set(self._active)
        trace_id = self._new_id(16, TraceId, active_ids)
        root_id = self._new_id(8, SpanId)
        stable_name = self._bounded_text(name or operation.value, _MAX_NAME_LENGTH)
        trace_links, trace_omitted_links = self._bounded_links(links)
        trace = _MutableTrace(
            trace_id,
            root_id,
            operation,
            stable_name,
            started,
            trace_links,
            trace_omitted_links,
        )
        root_attributes = self._bounded_attributes(attributes)
        trace.spans[root_id] = _MutableSpan(
            root_id,
            None,
            stable_name,
            started,
            root_attributes,
            trace_links,
            trace_omitted_links,
        )
        with self._lock:
            self._active[trace_id] = trace
        return trace

    def _start_span(
        self,
        trace: _MutableTrace,
        parent_span_id: SpanId,
        name: str,
        attributes: Mapping[str, AttributeValue] | None,
        links: Sequence[TraceLink],
    ) -> _MutableSpan:
        with self._lock:
            if trace.closed:
                message = "cannot add a span to a completed trace"
                raise RuntimeError(message)
            used_ids = set(trace.spans)
        span_id = self._new_id(8, SpanId, used_ids)
        span_links, omitted = self._bounded_links(links)
        span = _MutableSpan(
            span_id,
            parent_span_id,
            self._bounded_text(name, _MAX_NAME_LENGTH),
            self._clock(),
            self._bounded_attributes(attributes),
            span_links,
            omitted,
        )
        with self._lock:
            trace.spans[span_id] = span
        return span

    def _finish_span(self, trace: _MutableTrace, span: _MutableSpan, outcome: TraceOutcome) -> None:
        try:
            ended = self._clock()
            with self._lock:
                if trace.closed or span.ended is not None:
                    return
                span.ended = ended
                span.outcome = outcome
        except BaseException:
            self._note_internal_failure()

    def _finish_trace(self, trace: _MutableTrace, result: TraceResult) -> None:
        try:
            ended = self._clock()
            with self._lock:
                if trace.closed:
                    return
                root = trace.spans[trace.root_span_id]
                trace.closed = True
                trace.result = TraceResult(
                    result.outcome,
                    None if result.detail is None else self._bounded_text(result.detail, _MAX_DETAIL_LENGTH),
                    result.dispatch,
                    result.presentation,
                )
                root.ended = ended
                root.outcome = trace.result.outcome
                for span in trace.spans.values():
                    if span.ended is None:
                        span.ended = ended
                        span.outcome = TraceOutcome.ABANDONED
                frozen = self._freeze(trace, ended)
                self._active.pop(trace.trace_id, None)
                self._record(frozen, ended)
        except BaseException:
            with self._lock:
                trace.closed = True
                self._active.pop(trace.trace_id, None)
            self._note_internal_failure()

    def _freeze(self, trace: _MutableTrace, ended: float) -> RuntimeTrace:
        spans = tuple(
            RuntimeSpan(
                span.span_id,
                span.parent_span_id,
                span.name,
                max(0.0, span.started - trace.started),
                max(0.0, (span.ended or ended) - span.started),
                span.outcome or TraceOutcome.ABANDONED,
                span.attributes,
                span.links,
                span.omitted_links,
            )
            for span in trace.spans.values()
        )
        return RuntimeTrace(
            trace.trace_id,
            trace.root_span_id,
            trace.operation,
            trace.name,
            max(0.0, trace.started - self._started),
            max(0.0, ended - trace.started),
            trace.result or TraceResult(TraceOutcome.ABANDONED),
            spans,
            trace.links,
            trace.omitted_links,
            trace.deadline_missed,
        )

    def _record(self, trace: RuntimeTrace, ended: float) -> None:
        key = self._aggregate_key(trace)
        self._lifetime.setdefault(key, _Histogram.empty(self._bounds)).observe(trace.duration)

        slice_index = math.floor(ended / self._slice_seconds)
        if not self._window or self._window[-1].index != slice_index:
            self._window.append(_WindowSlice(slice_index))
        self._window[-1].histograms.setdefault(key, _Histogram.empty(self._bounds)).observe(trace.duration)
        for span in trace.spans:
            if span.span_id == trace.root_span_id:
                continue
            span_key = self._span_aggregate_key(trace, span)
            self._span_lifetime.setdefault(span_key, _Histogram.empty(self._bounds)).observe(span.duration)
            self._window[-1].span_histograms.setdefault(span_key, _Histogram.empty(self._bounds)).observe(span.duration)
        self._trim_window(slice_index)

        retained = False
        selected = False
        if trace.result.outcome is not TraceOutcome.COMPLETED:
            selected = True
            retained |= self._append(self._failed, trace)
        if trace.duration >= self._slow_threshold:
            selected = True
            retained |= self._append(self._slow, trace)
        if trace.deadline_missed:
            selected = True
            retained |= self._append(self._deadline_misses, trace)

        ordinary = (
            trace.result.outcome is TraceOutcome.COMPLETED
            and not trace.deadline_missed
            and trace.duration < self._slow_threshold
        )
        if ordinary:
            sampled = self._sample_rate >= 1 or (self._sample_rate > 0 and self._sample_source() < self._sample_rate)
            if sampled:
                selected = True
                retained |= self._append(self._recent, trace)
            else:
                self._sampled_out += 1

        if selected and not retained:
            self._dropped_traces += 1

    def _aggregate_key(self, trace: RuntimeTrace) -> AggregateKey:
        dispatch = trace.result.dispatch
        key = AggregateKey(
            trace.operation,
            trace.name,
            trace.result.outcome,
            trace.result.detail,
            None if dispatch is None else dispatch.disposition,
            None if dispatch is None else dispatch.action,
            trace.result.presentation if dispatch is None else dispatch.presentation,
        )
        if key in self._lifetime or len(self._lifetime) < self._max_aggregate_keys:
            return key
        if self._overflow_key is None:
            self._overflow_key = AggregateKey(
                None,
                _OVERFLOW_AGGREGATE_NAME,
                None,
                None,
                None,
                None,
                None,
            )
        return self._overflow_key

    def _append(self, target: deque[RuntimeTrace], trace: RuntimeTrace) -> bool:
        if target.maxlen == 0:
            return False
        if len(target) == target.maxlen:
            self._evicted += 1
        target.append(trace)
        return True

    def _span_aggregate_key(self, trace: RuntimeTrace, span: RuntimeSpan) -> SpanAggregateKey:
        key = SpanAggregateKey(trace.operation, trace.name, span.name, span.outcome)
        if key in self._span_lifetime or len(self._span_lifetime) < self._max_span_aggregate_keys:
            return key
        if self._span_overflow_key is None:
            self._span_overflow_key = SpanAggregateKey(None, _OVERFLOW_AGGREGATE_NAME, _OVERFLOW_AGGREGATE_NAME, None)
        return self._span_overflow_key

    def _trim_window(self, current_index: int) -> None:
        earliest = current_index - self._window_slices + 1
        while self._window and self._window[0].index < earliest:
            self._window.popleft()

    def _window_histograms(self, now: float) -> dict[AggregateKey, _Histogram]:
        current_index = math.floor(now / self._slice_seconds)
        self._trim_window(current_index)
        merged: dict[AggregateKey, _Histogram] = {}
        for window_slice in self._window:
            for key, histogram in window_slice.histograms.items():
                merged.setdefault(key, _Histogram.empty(self._bounds)).merge(histogram)
        return merged

    def _span_window_histograms(self) -> dict[SpanAggregateKey, _Histogram]:
        merged: dict[SpanAggregateKey, _Histogram] = {}
        for window_slice in self._window:
            for key, histogram in window_slice.span_histograms.items():
                merged.setdefault(key, _Histogram.empty(self._bounds)).merge(histogram)
        return merged

    def _active_snapshot(self, trace: _MutableTrace, now: float) -> ActiveTraceSnapshot:
        current = tuple(
            ActiveSpanSnapshot(
                span.span_id,
                span.parent_span_id,
                span.name,
                max(0.0, span.started - trace.started),
                max(0.0, now - span.started),
            )
            for span in trace.spans.values()
            if span.ended is None and span.span_id != trace.root_span_id
        )
        return ActiveTraceSnapshot(
            trace.trace_id,
            trace.operation,
            trace.name,
            max(0.0, trace.started - self._started),
            max(0.0, now - trace.started),
            current,
        )

    def _bounded_attributes(self, attributes: Mapping[str, AttributeValue] | None) -> tuple[SpanAttribute, ...]:
        if attributes is None:
            return ()

        retained: list[SpanAttribute] = []
        rejected = 0
        for key, value in attributes.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_ATTRIBUTE_KEY_LENGTH:
                rejected += 1
                continue
            if len(retained) >= _MAX_ATTRIBUTES:
                rejected += 1
                continue
            if value is None:
                retained.append(SpanAttribute(key, None))
                continue
            if not isinstance(value, (str, int, float, bool)):
                rejected += 1
                continue
            if isinstance(value, float) and not math.isfinite(value):
                rejected += 1
                continue
            if isinstance(value, str) and len(value) > _MAX_ATTRIBUTE_STRING_LENGTH:
                retained.append(SpanAttribute(key, value[:_MAX_ATTRIBUTE_STRING_LENGTH]))
                rejected += 1
                continue
            retained.append(SpanAttribute(key, value))

        with self._lock:
            self._rejected_attributes += rejected
        return tuple(retained)

    def _set_span_attribute(
        self,
        trace: _MutableTrace,
        span: _MutableSpan,
        key: str,
        value: AttributeValue,
    ) -> None:
        try:
            with self._lock:
                if trace.closed or span.ended is not None:
                    return
                attributes = {attribute.key: attribute.value for attribute in span.attributes}
                attributes[key] = value
                span.attributes = self._bounded_attributes(attributes)
        except BaseException:
            self._note_internal_failure()

    def _bounded_links(self, links: Sequence[TraceLink]) -> tuple[tuple[TraceLink, ...], int]:
        retained: list[TraceLink] = []
        omitted = 0
        for link in links:
            if link in retained:
                continue
            if len(retained) < self._max_links:
                retained.append(link)
            else:
                omitted += 1
        return tuple(retained), omitted

    @staticmethod
    def _bounded_text(value: str, maximum: int) -> str:
        return value[:maximum]

    def _note_internal_failure(self) -> None:
        with self._lock:
            self._internal_failures += 1
