"""Connect work, log lines, and error reports through shared correlation IDs.

A correlation ID groups the logs and failures from one request or background
operation. Entry points bind it directly or use correlation_scope; error reports
can then retain that operation's recent log lines and show a shorter reference
to the user. This works without OpenTelemetry installed.

Optional OpenTelemetry support adds spans and metrics exported by each process.
A trace ID can supply the correlation ID when none is bound, while propagated
trace context connects parent work to schematic worker subprocesses.
"""

import logging
import os
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, override
from uuid import uuid4

type SpanAttribute = str | bool | int | float


class TraceSurface(StrEnum):
    """Low-cardinality application surfaces attached to telemetry spans."""

    APPLICATION_COMMAND = "application_command"
    BACKGROUND_LOOP = "background_loop"
    BACKGROUND_WORK = "background_work"
    DISCORD_ROUTE = "discord_route"
    MODAL = "modal"
    PREFIX_COMMAND = "prefix_command"
    RUNNING_MESSAGE = "running_message"
    VIEW = "view"


class _SpanContext(Protocol):
    is_valid: bool
    trace_id: int
    span_id: int


class Span(Protocol):
    def record_exception(self, error: BaseException) -> None: ...

    def set_status(self, status: object) -> None: ...

    def set_attribute(self, name: str, value: SpanAttribute) -> None: ...

    def get_span_context(self) -> _SpanContext: ...


class Tracer(Protocol):
    def start_as_current_span(
        self,
        name: str,
        *,
        context: object | None = None,
        attributes: Mapping[str, SpanAttribute] | None = None,
    ) -> AbstractContextManager[Span]: ...


class _Counter(Protocol):
    def add(self, value: int, attributes: Mapping[str, SpanAttribute]) -> None: ...


class _Histogram(Protocol):
    def record(self, value: float, attributes: Mapping[str, SpanAttribute]) -> None: ...


class _Gauge(Protocol):
    def set(self, value: int | float, attributes: Mapping[str, SpanAttribute]) -> None: ...


class Meter(Protocol):
    def create_counter(self, name: str) -> _Counter: ...

    def create_histogram(self, name: str) -> _Histogram: ...

    def create_gauge(self, name: str) -> _Gauge: ...


class _Propagator(Protocol):
    def inject(self, carrier: dict[str, str]) -> None: ...

    def extract(self, carrier: Mapping[str, str]) -> object: ...


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Tracing and metric providers registered by process startup."""

    pid: int
    tracer: Tracer
    worker_tracer: Tracer
    meter: Meter
    propagator: _Propagator
    current_span: Callable[[], Span]
    error_status: Callable[[], object]

    def activate(self) -> None:
        """Register these providers for work in this process."""
        global _ACTIVE
        _ACTIVE = self

    def deactivate(self) -> None:
        """Clear these providers and their metric instruments if still active."""
        global _ACTIVE
        with _metric_lock:
            if _ACTIVE is not self:
                return
            _ACTIVE = None
            _counters.clear()
            _histograms.clear()
            _gauges.clear()


CORRELATION_REFERENCE_LENGTH = 12
"""Length of the error reference shown to users.

Twelve hex characters provide 48 bits. With 10,000 random references, the chance
of any collision is roughly 2e-7; eight characters would raise it to about 1%.
"""

# Request-scoped correlation id, bound by the API's RequestContextMiddleware. When set it wins
# over the trace-derived id so a whole request shares one id even without the observability extra.
_bound_correlation_id: ContextVar[str | None] = ContextVar("squid_bound_correlation_id", default=None)


def bind_correlation_id(value: str) -> Token[str | None]:
    """Bind a request-scoped correlation id consulted by ``correlation_id``."""
    return _bound_correlation_id.set(value)


def unbind_correlation_id(token: Token[str | None]) -> None:
    """Restore the correlation binding captured at bind time."""
    _bound_correlation_id.reset(token)


def active_trace_id() -> str | None:
    """Return the active 128-bit OpenTelemetry trace id, or ``None`` without telemetry."""
    return _current_trace_id()


class TraceSpan:
    """A span wrapper whose methods do nothing when telemetry is unavailable."""

    def __init__(self, span: Span | None = None, error_status: Callable[[], object] | None = None) -> None:
        self._span = span
        self._error_status = error_status

    def set_error(self, error: BaseException | None = None) -> None:
        """Mark the span failed, recording an exception when one is available."""
        if self._span is None or self._error_status is None:
            return
        if error is not None:
            self._span.record_exception(error)
        self._span.set_status(self._error_status())

    def set_attribute(self, name: str, value: SpanAttribute) -> None:
        """Attach one low-cardinality attribute when a real span is active."""
        if self._span is not None:
            self._span.set_attribute(name, value)


class TraceContextFilter(logging.Filter):
    """Attach the active trace and span IDs without overwriting propagated child values."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        context = _current_trace_context()
        trace_id, span_id = context if context is not None else (None, None)
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id
        if not hasattr(record, "span_id"):
            record.span_id = span_id
        if not hasattr(record, "request_id"):
            record.request_id = _bound_correlation_id.get()
        return True


CORRELATION_BUFFER_HANDLER = "correlation_buffer"
"""dictConfig name under which `CorrelatedLogBuffer` is installed, when it is enabled."""


class CorrelatedLogBuffer(logging.Handler):
    """Keep recent formatted log lines for each correlation ID.

    Formatting records immediately avoids retaining their arguments and exception
    objects. Each correlation keeps at most `max_records` lines. When the number of
    correlations exceeds `max_correlations`, evict the one least recently written.
    Reading a snapshot does not update that order.
    """

    def __init__(self, *, max_records: int = 50, max_correlations: int = 256) -> None:
        super().__init__()
        self._max_records = max_records
        self._max_correlations = max_correlations
        self._buffers: OrderedDict[str, deque[str]] = OrderedDict()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        correlation = getattr(record, "request_id", None)
        if not isinstance(correlation, str):
            return
        try:
            formatted = self.format(record)
        except Exception:
            self.handleError(record)
            return
        # `acquire`/`release` rather than `with self.lock`: Handler.lock is optional, and these
        # tolerate its absence the way the rest of logging does.
        self.acquire()
        try:
            buffer = self._buffers.get(correlation)
            if buffer is None:
                buffer = deque(maxlen=self._max_records)
                self._buffers[correlation] = buffer
                while len(self._buffers) > self._max_correlations:
                    self._buffers.popitem(last=False)
            else:
                self._buffers.move_to_end(correlation)
            buffer.append(formatted)
        finally:
            self.release()

    def snapshot(self, correlation: str) -> tuple[str, ...]:
        """Read a correlation's buffered lines without consuming them.

        Log-driven capture uses this rather than `drain`: one run can log several failures, and
        the first of them must not take the context away from the rest, or from a later explicit
        capture on the same correlation.
        """
        self.acquire()
        try:
            buffer = self._buffers.get(correlation)
            return tuple(buffer) if buffer is not None else ()
        finally:
            self.release()

    def drain(self, correlation: str) -> tuple[str, ...]:
        """Remove and return a correlation's buffered lines, or an empty tuple if absent."""
        self.acquire()
        try:
            buffer = self._buffers.pop(correlation, None)
        finally:
            self.release()
        return tuple(buffer) if buffer is not None else ()


def correlated_log_buffer() -> CorrelatedLogBuffer | None:
    """Return this process's installed log buffer, if logging was configured with one."""
    handler = logging.getHandlerByName(CORRELATION_BUFFER_HANDLER)
    return handler if isinstance(handler, CorrelatedLogBuffer) else None


_ACTIVE: Telemetry | None = None
_metric_lock = threading.Lock()
_counters: dict[str, _Counter] = {}
_histograms: dict[str, _Histogram] = {}
_gauges: dict[str, _Gauge] = {}


def active_telemetry() -> Telemetry | None:
    """Return this process's active telemetry capabilities, if configured."""
    active = _ACTIVE
    return active if active is not None and active.pid == os.getpid() else None


def correlation_id() -> str:
    """Return the bound correlation ID, the active trace ID, or a new random fallback.

    Without a binding or active trace, each call generates a different ID. Use
    `correlation_scope()` when several operations need to share one.
    """
    bound = _bound_correlation_id.get()
    if bound is not None:
        return bound
    trace_id = _current_trace_id()
    return trace_id if trace_id is not None else uuid4().hex[:12]


@contextmanager
def correlation_scope() -> Generator[str]:
    """Bind and yield a correlation ID until the block exits.

    Nested scopes reuse the outer ID. This lets both invocation paths of a Discord
    hybrid command report the same failure reference.
    """
    existing = _bound_correlation_id.get()
    if existing is not None:
        yield existing
        return
    resolved = correlation_id()
    token = bind_correlation_id(resolved)
    try:
        yield resolved
    finally:
        unbind_correlation_id(token)


def correlation_reference(correlation_id: str) -> str:
    """Return the first 12 characters of a correlation ID for display."""
    return correlation_id[:CORRELATION_REFERENCE_LENGTH]


@contextmanager
def trace_span(name: str, attributes: Mapping[str, SpanAttribute] | None = None) -> Generator[TraceSpan]:
    """Start a span that records escaping exceptions, or yield a no-op wrapper without telemetry."""
    telemetry = active_telemetry()
    if telemetry is None:
        yield TraceSpan()
        return
    with telemetry.tracer.start_as_current_span(name, attributes=dict(attributes or {})) as span:
        facade = TraceSpan(span, telemetry.error_status)
        try:
            yield facade
        except BaseException as exc:
            facade.set_error(exc)
            raise


def trace_context_headers() -> dict[str, str]:
    """Return the active W3C trace context as worker-protocol headers."""
    telemetry = active_telemetry()
    if telemetry is None:
        return {}
    headers: dict[str, str] = {}
    telemetry.propagator.inject(headers)
    return headers


@contextmanager
def extracted_trace_span(
    name: str,
    carrier: Mapping[str, str],
    attributes: Mapping[str, SpanAttribute] | None = None,
) -> Generator[TraceSpan]:
    """Extract a parent context and start a child span, tolerating absent propagation."""
    telemetry = active_telemetry()
    if telemetry is None:
        yield TraceSpan()
        return
    parent = telemetry.propagator.extract(carrier)
    with telemetry.worker_tracer.start_as_current_span(name, context=parent, attributes=dict(attributes or {})) as span:
        facade = TraceSpan(span, telemetry.error_status)
        try:
            yield facade
        except BaseException as exc:
            facade.set_error(exc)
            raise


def record_current_exception(error: BaseException) -> None:
    """Record an exception on the active span when one exists."""
    telemetry = active_telemetry()
    if telemetry is None:
        return
    TraceSpan(telemetry.current_span(), telemetry.error_status).set_error(error)


def add_counter(name: str, *, attributes: Mapping[str, SpanAttribute] | None = None, value: int = 1) -> None:
    """Add to an application counter when the metrics SDK is configured."""
    telemetry = active_telemetry()
    if telemetry is None:
        return
    with _metric_lock:
        counter = _counters.get(name)
        if counter is None:
            counter = telemetry.meter.create_counter(name)
            _counters[name] = counter
    counter.add(value, dict(attributes or {}))


def record_histogram(name: str, value: float, *, attributes: Mapping[str, SpanAttribute] | None = None) -> None:
    """Record an application histogram value when the metrics SDK is configured."""
    telemetry = active_telemetry()
    if telemetry is None:
        return
    with _metric_lock:
        histogram = _histograms.get(name)
        if histogram is None:
            histogram = telemetry.meter.create_histogram(name)
            _histograms[name] = histogram
    histogram.record(value, dict(attributes or {}))


def record_gauge(name: str, value: int | float, *, attributes: Mapping[str, SpanAttribute] | None = None) -> None:
    """Set the current value of an application gauge when metrics are configured."""
    telemetry = active_telemetry()
    if telemetry is None:
        return
    with _metric_lock:
        gauge = _gauges.get(name)
        if gauge is None:
            gauge = telemetry.meter.create_gauge(name)
            _gauges[name] = gauge
    gauge.set(value, dict(attributes or {}))


def _current_trace_id() -> str | None:
    context = _current_trace_context()
    return context[0] if context is not None else None


def _current_trace_context() -> tuple[str, str] | None:
    telemetry = active_telemetry()
    if telemetry is None:
        return None
    context = telemetry.current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def install_trace_context_log_filter() -> None:
    """Attach trace/request-id log correlation to every named handler, at most once each."""
    for handler_name in logging.getHandlerNames():
        handler = logging.getHandlerByName(handler_name)
        if handler is not None and not any(isinstance(existing, TraceContextFilter) for existing in handler.filters):
            handler.addFilter(TraceContextFilter())


__all__ = [
    "CORRELATION_BUFFER_HANDLER",
    "CORRELATION_REFERENCE_LENGTH",
    "CorrelatedLogBuffer",
    "Meter",
    "Span",
    "Telemetry",
    "TraceContextFilter",
    "TraceSpan",
    "TraceSurface",
    "Tracer",
    "active_telemetry",
    "active_trace_id",
    "add_counter",
    "bind_correlation_id",
    "correlated_log_buffer",
    "correlation_id",
    "correlation_reference",
    "correlation_scope",
    "extracted_trace_span",
    "install_trace_context_log_filter",
    "record_current_exception",
    "record_gauge",
    "record_histogram",
    "trace_context_headers",
    "trace_span",
    "unbind_correlation_id",
]
