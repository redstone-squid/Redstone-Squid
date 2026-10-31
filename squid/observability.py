"""Process-local OpenTelemetry initialization with an optional runtime dependency."""

import logging
import os
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast, override
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

if TYPE_CHECKING:
    from fastapi import FastAPI

    from squid.config import ObservabilityConfig

logger = logging.getLogger(__name__)
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


class _Span(Protocol):
    def record_exception(self, error: BaseException) -> None: ...

    def set_status(self, status: object) -> None: ...

    def set_attribute(self, name: str, value: SpanAttribute) -> None: ...

    def get_span_context(self) -> _SpanContext: ...


class _Tracer(Protocol):
    def start_as_current_span(
        self,
        name: str,
        *,
        context: object | None = None,
        attributes: Mapping[str, SpanAttribute] | None = None,
    ) -> AbstractContextManager[_Span]: ...


class _Counter(Protocol):
    def add(self, value: int, attributes: Mapping[str, SpanAttribute]) -> None: ...


class _Histogram(Protocol):
    def record(self, value: float, attributes: Mapping[str, SpanAttribute]) -> None: ...


class _Gauge(Protocol):
    def set(self, value: int | float, attributes: Mapping[str, SpanAttribute]) -> None: ...


class _Meter(Protocol):
    def create_counter(self, name: str) -> _Counter: ...

    def create_histogram(self, name: str) -> _Histogram: ...

    def create_gauge(self, name: str) -> _Gauge: ...


class _Propagator(Protocol):
    def inject(self, carrier: dict[str, str]) -> None: ...

    def extract(self, carrier: Mapping[str, str]) -> object: ...


@dataclass(frozen=True, slots=True)
class _Telemetry:
    pid: int
    tracer: _Tracer
    worker_tracer: _Tracer
    meter: _Meter
    propagator: _Propagator
    current_span: Callable[[], _Span]
    error_status: Callable[[], object]
    instrument_api_app: Callable[[FastAPI], None]


CORRELATION_REFERENCE_LENGTH = 12
"""Hex characters shown to users: 48 bits, and the width of the untraced fallback.

The requirement is that one reported reference resolves to one incident within the retention
window, so the bound is `N / 2**48`: at 10 000 unexpected errors per window that is ~4e-11, and
even the stricter all-pairs-distinct bound, `N**2 / 2**49`, is ~2e-7. Eight characters would put
that all-pairs bound near 1% for the same volume, which is why the prefix is not shortened further.
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
    """Small transport-facing facade over an optional OpenTelemetry span."""

    def __init__(self, span: _Span | None = None, error_status: Callable[[], object] | None = None) -> None:
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
    """Keep the most recent log lines per correlation ID so an error can be explained.

    Records are formatted on arrival rather than held: a `LogRecord` keeps its `args` and
    `exc_info` alive, so buffering thousands of them would pin arbitrary application objects for
    as long as the correlation lives. Formatting eagerly costs a little CPU on the logging path
    and bounds the memory to text we already know we want.

    Both dimensions are bounded. `max_correlations` evicts whole correlations in insertion order,
    which for request-scoped IDs is close enough to least-recently-used, and `max_records` caps
    each one, so a single pathological loop cannot displace every other correlation's tail.
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
        """Take and forget everything buffered under `correlation`.

        Draining rather than copying keeps a captured error from being reported twice with the
        same tail, and releases the memory at the one moment we know the correlation is over.
        """
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


class ObservabilityHandle:
    """Own one process's telemetry provider shutdown."""

    def __init__(self, shutdown: Callable[[], None] | None = None) -> None:
        self._shutdown = shutdown
        self._closed = False
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        """Flush and stop telemetry once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            shutdown = self._shutdown
        if shutdown is not None:
            shutdown()


_NOOP_HANDLE = ObservabilityHandle()
_configuration_lock = threading.Lock()
_configured_pid: int | None = None
_configured_handle: ObservabilityHandle | None = None
_ACTIVE: _Telemetry | None = None
_metric_lock = threading.Lock()
_counters: dict[str, _Counter] = {}
_histograms: dict[str, _Histogram] = {}
_gauges: dict[str, _Gauge] = {}


def _telemetry() -> _Telemetry | None:
    """Return this process's active telemetry capabilities, if configured."""
    active = _ACTIVE
    return active if active is not None and active.pid == os.getpid() else None


def configure_observability(config: ObservabilityConfig, *, service_name: str) -> ObservabilityHandle:
    """Configure OTLP trace export once in the current process.

    Disabled deployments return before importing OpenTelemetry. The configuration service
    name is a deployment-owned base; ``service_name`` distinguishes the process component.
    """
    if not config.enabled:
        return _NOOP_HANDLE

    process_id = os.getpid()
    global _configured_handle, _configured_pid
    with _configuration_lock:
        if _configured_pid == process_id and _configured_handle is not None:
            return _configured_handle
        if _configured_pid is not None and _configured_pid != process_id:
            msg = "Observability was configured before this process forked."
            raise RuntimeError(msg)

        try:
            handle = _configure_otel(config, service_name=service_name)
        except ModuleNotFoundError as exc:
            if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
                raise
            logger.warning(
                "Observability is enabled but the optional 'observability' extra is not installed; tracing is disabled."
            )
            handle = ObservabilityHandle()

        _configured_pid = process_id
        _configured_handle = handle
        return handle


def instrument_api_app(app: FastAPI, config: ObservabilityConfig) -> None:
    """Instrument one FastAPI application when observability is enabled and installed."""
    if not config.enabled:
        return
    telemetry = _telemetry()
    if telemetry is None:
        return
    telemetry.instrument_api_app(app)


def correlation_id() -> str:
    """Return the request-scoped id when bound, else the active trace ID, else a local fallback."""
    bound = _bound_correlation_id.get()
    if bound is not None:
        return bound
    trace_id = _current_trace_id()
    return trace_id if trace_id is not None else uuid4().hex[:12]


@contextmanager
def correlation_scope() -> Generator[str]:
    """Bind one correlation ID for the duration of a unit of work, and yield it.

    Re-entrant on purpose: a nested scope keeps the outer binding rather than minting a second
    ID. A hybrid command arrives through the application command tree and is then invoked through
    the prefix path, so both scopes open around the same invocation and must agree.
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
    """Shorten a correlation ID for display without changing what is stored or sent.

    A 32-hex trace ID becomes its first 12 characters; the untraced fallback is already that
    width, so this is the identity function for it. Both paths therefore look the same to a user
    reading an error card and to whoever they report it to.
    """
    return correlation_id[:CORRELATION_REFERENCE_LENGTH]


@contextmanager
def trace_span(name: str, attributes: Mapping[str, SpanAttribute] | None = None) -> Generator[TraceSpan]:
    """Start an application-edge span, or yield a no-op facade without the extra."""
    telemetry = _telemetry()
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
    telemetry = _telemetry()
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
    telemetry = _telemetry()
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
    telemetry = _telemetry()
    if telemetry is None:
        return
    TraceSpan(telemetry.current_span(), telemetry.error_status).set_error(error)


def add_counter(name: str, *, attributes: Mapping[str, SpanAttribute] | None = None, value: int = 1) -> None:
    """Add to an application counter when the metrics SDK is configured."""
    telemetry = _telemetry()
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
    telemetry = _telemetry()
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
    telemetry = _telemetry()
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
    telemetry = _telemetry()
    if telemetry is None:
        return None
    context = telemetry.current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def _configure_otel(config: ObservabilityConfig, *, service_name: str) -> ObservabilityHandle:
    """Build the SDK pipeline after the caller has established process ownership."""
    from opentelemetry import metrics, propagate, trace  # pyright: ignore[reportMissingImports]
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # pyright: ignore[reportMissingImports]
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.aiohttp_client import (  # pyright: ignore[reportMissingImports]
        AioHttpClientInstrumentor,
    )
    from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingImports]
        FastAPIInstrumentor,
    )
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # pyright: ignore[reportMissingImports]
    from opentelemetry.instrumentation.sqlalchemy import (  # pyright: ignore[reportMissingImports]
        SQLAlchemyInstrumentor,
    )
    from opentelemetry.sdk.metrics import MeterProvider  # pyright: ignore[reportMissingImports]
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # pyright: ignore[reportMissingImports]
    from opentelemetry.sdk.resources import Resource  # pyright: ignore[reportMissingImports]
    from opentelemetry.sdk.trace import TracerProvider  # pyright: ignore[reportMissingImports]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # pyright: ignore[reportMissingImports]
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio  # pyright: ignore[reportMissingImports]
    from opentelemetry.trace.status import Status, StatusCode  # pyright: ignore[reportMissingImports]

    component = service_name.strip()
    if not component:
        msg = "The observability process service name must not be empty."
        raise ValueError(msg)
    resource = Resource.create(_resource_attributes(config, component))
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBasedTraceIdRatio(config.sample_ratio),
        shutdown_on_exit=False,
    )
    exporter = OTLPSpanExporter(
        endpoint=_signal_endpoint(str(config.endpoint), "traces"),
        headers={name: value.get_secret_value() for name, value in config.headers.items()},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    metric_exporter = OTLPMetricExporter(
        endpoint=_signal_endpoint(str(config.endpoint), "metrics"),
        headers={name: value.get_secret_value() for name, value in config.headers.items()},
    )
    metric_provider = MeterProvider(
        metric_readers=(PeriodicExportingMetricReader(metric_exporter),),
        resource=resource,
        shutdown_on_exit=False,
    )
    metrics.set_meter_provider(metric_provider)
    active = _Telemetry(
        pid=os.getpid(),
        tracer=cast(_Tracer, trace.get_tracer("squid")),
        worker_tracer=cast(_Tracer, trace.get_tracer("squid.worker")),
        meter=cast(_Meter, metric_provider.get_meter("squid")),
        propagator=propagate,
        current_span=cast(Callable[[], _Span], trace.get_current_span),
        error_status=lambda: Status(StatusCode.ERROR),
        instrument_api_app=FastAPIInstrumentor.instrument_app,
    )
    global _ACTIVE
    _ACTIVE = active
    SQLAlchemyInstrumentor().instrument()
    AioHttpClientInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    install_trace_context_log_filter()

    def shutdown() -> None:
        _clear_telemetry(active)
        try:
            provider.shutdown()
        finally:
            metric_provider.shutdown()

    return ObservabilityHandle(shutdown)


def _clear_telemetry(active: _Telemetry) -> None:
    """Clear one stopped provider's capabilities and instruments if it is still active."""
    global _ACTIVE
    with _metric_lock:
        if _ACTIVE is not active:
            return
        _ACTIVE = None
        _counters.clear()
        _histograms.clear()
        _gauges.clear()


def _resource_attributes(config: ObservabilityConfig, component: str) -> dict[str, str]:
    """Build low-cardinality deployment identity shared by traces and metrics."""
    attributes = {
        "service.name": f"{config.service_name}-{component}",
        "deployment.environment.name": config.environment,
    }
    if config.release is not None:
        attributes["service.version"] = config.release
    return attributes


def install_trace_context_log_filter() -> None:
    """Attach trace/request-id log correlation to every named handler, at most once each."""
    for handler_name in logging.getHandlerNames():
        handler = logging.getHandlerByName(handler_name)
        if handler is not None and not any(isinstance(existing, TraceContextFilter) for existing in handler.filters):
            handler.addFilter(TraceContextFilter())


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """Resolve the generic OTLP HTTP base URL to one signal endpoint."""
    parsed = urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    signal_path = f"/v1/{signal}"
    path = base_path if base_path.endswith(signal_path) else f"{base_path}{signal_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


__all__ = [
    "CORRELATION_BUFFER_HANDLER",
    "CORRELATION_REFERENCE_LENGTH",
    "CorrelatedLogBuffer",
    "ObservabilityHandle",
    "TraceContextFilter",
    "TraceSpan",
    "TraceSurface",
    "active_trace_id",
    "add_counter",
    "bind_correlation_id",
    "configure_observability",
    "correlated_log_buffer",
    "correlation_id",
    "correlation_reference",
    "correlation_scope",
    "extracted_trace_span",
    "install_trace_context_log_filter",
    "instrument_api_app",
    "record_current_exception",
    "record_gauge",
    "record_histogram",
    "trace_context_headers",
    "trace_span",
    "unbind_correlation_id",
]
