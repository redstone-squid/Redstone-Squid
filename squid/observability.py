"""Process-local OpenTelemetry initialization with an optional runtime dependency."""

import logging
import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from squid.config import ObservabilityConfig

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)
type SpanAttribute = str | bool | int | float


class TraceSpan:
    """Small transport-facing facade over an optional OpenTelemetry span."""

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def set_error(self, error: BaseException | None = None) -> None:
        """Mark the span failed, recording an exception when one is available."""
        if self._span is None:
            return
        if error is not None:
            self._span.record_exception(error)
        from opentelemetry.trace.status import Status, StatusCode  # pyright: ignore[reportMissingImports]

        self._span.set_status(Status(StatusCode.ERROR))

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
        return True


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
_telemetry_active_pid: int | None = None
_meter: Any | None = None
_metric_lock = threading.Lock()
_counters: dict[str, Any] = {}
_histograms: dict[str, Any] = {}


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


def instrument_api_app(app: "FastAPI", config: ObservabilityConfig) -> None:
    """Instrument one FastAPI application when observability is enabled and installed."""
    if not config.enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingImports]
            FastAPIInstrumentor,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        return
    FastAPIInstrumentor.instrument_app(app)


def correlation_id() -> str:
    """Return the active 128-bit trace ID, with a local fallback when tracing is absent."""
    trace_id = _current_trace_id()
    return trace_id if trace_id is not None else uuid4().hex[:12]


@contextmanager
def trace_span(name: str, attributes: Mapping[str, SpanAttribute] | None = None) -> Iterator[TraceSpan]:
    """Start an application-edge span, or yield a no-op facade without the extra."""
    if _telemetry_active_pid != os.getpid():
        yield TraceSpan()
        return
    try:
        from opentelemetry import trace  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        yield TraceSpan()
        return

    tracer = trace.get_tracer("squid")
    with tracer.start_as_current_span(name, attributes=dict(attributes or {})) as span:
        facade = TraceSpan(span)
        try:
            yield facade
        except BaseException as exc:
            facade.set_error(exc)
            raise


def inject_trace_context(carrier: dict[str, Any]) -> None:
    """Inject the active W3C trace context into a JSON-compatible carrier."""
    if _telemetry_active_pid != os.getpid():
        return
    try:
        from opentelemetry import propagate  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        return
    headers: dict[str, str] = {}
    propagate.inject(headers)
    carrier.update(headers)


@contextmanager
def extracted_trace_span(
    name: str,
    carrier: Mapping[str, Any],
    attributes: Mapping[str, SpanAttribute] | None = None,
) -> Iterator[TraceSpan]:
    """Extract a parent context and start a child span, tolerating absent propagation."""
    if _telemetry_active_pid != os.getpid():
        yield TraceSpan()
        return
    try:
        from opentelemetry import propagate, trace  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        yield TraceSpan()
        return
    headers = {name: value for name, value in carrier.items() if isinstance(value, str)}
    parent = propagate.extract(headers)
    tracer = trace.get_tracer("squid.worker")
    with tracer.start_as_current_span(name, context=parent, attributes=dict(attributes or {})) as span:
        facade = TraceSpan(span)
        try:
            yield facade
        except BaseException as exc:
            facade.set_error(exc)
            raise


def record_current_exception(error: BaseException) -> None:
    """Record an exception on the active span when one exists."""
    if _telemetry_active_pid != os.getpid():
        return
    try:
        from opentelemetry import trace  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        return
    TraceSpan(trace.get_current_span()).set_error(error)


def add_counter(name: str, *, attributes: Mapping[str, SpanAttribute] | None = None, value: int = 1) -> None:
    """Add to an application counter when the metrics SDK is configured."""
    if _meter is None:
        return
    with _metric_lock:
        counter = _counters.get(name)
        if counter is None:
            counter = _meter.create_counter(name)
            _counters[name] = counter
    counter.add(value, dict(attributes or {}))


def record_histogram(name: str, value: float, *, attributes: Mapping[str, SpanAttribute] | None = None) -> None:
    """Record an application histogram value when the metrics SDK is configured."""
    if _meter is None:
        return
    with _metric_lock:
        histogram = _histograms.get(name)
        if histogram is None:
            histogram = _meter.create_histogram(name)
            _histograms[name] = histogram
    histogram.record(value, dict(attributes or {}))


def _current_trace_id() -> str | None:
    context = _current_trace_context()
    return context[0] if context is not None else None


def _current_trace_context() -> tuple[str, str] | None:
    if _telemetry_active_pid != os.getpid():
        return None
    try:
        from opentelemetry import trace  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def _configure_otel(config: ObservabilityConfig, *, service_name: str) -> ObservabilityHandle:
    """Build the SDK pipeline after the caller has established process ownership."""
    from opentelemetry import metrics, trace  # pyright: ignore[reportMissingImports]
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # pyright: ignore[reportMissingImports]
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.aiohttp_client import (  # pyright: ignore[reportMissingImports]
        AioHttpClientInstrumentor,
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

    component = service_name.strip()
    if not component:
        msg = "The observability process service name must not be empty."
        raise ValueError(msg)
    resource = Resource.create({"service.name": f"{config.service_name}-{component}"})
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBasedTraceIdRatio(config.sample_ratio),
        shutdown_on_exit=False,
    )
    exporter = OTLPSpanExporter(
        endpoint=_trace_endpoint(str(config.endpoint)),
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
    global _meter, _telemetry_active_pid
    _meter = metric_provider.get_meter("squid")
    _telemetry_active_pid = os.getpid()
    SQLAlchemyInstrumentor().instrument()
    AioHttpClientInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    _install_trace_log_filter()

    def shutdown() -> None:
        try:
            provider.shutdown()
        finally:
            metric_provider.shutdown()

    return ObservabilityHandle(shutdown)


def _install_trace_log_filter() -> None:
    trace_filter = TraceContextFilter()
    for handler_name in logging.getHandlerNames():
        handler = logging.getHandlerByName(handler_name)
        if handler is not None:
            handler.addFilter(trace_filter)


def _trace_endpoint(endpoint: str) -> str:
    """Resolve the generic OTLP HTTP base URL to the traces signal endpoint."""
    return _signal_endpoint(endpoint, "traces")


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """Resolve the generic OTLP HTTP base URL to one signal endpoint."""
    parsed = urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    signal_path = f"/v1/{signal}"
    path = base_path if base_path.endswith(signal_path) else f"{base_path}{signal_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


__all__ = [
    "ObservabilityHandle",
    "TraceContextFilter",
    "TraceSpan",
    "add_counter",
    "configure_observability",
    "correlation_id",
    "extracted_trace_span",
    "inject_trace_context",
    "instrument_api_app",
    "record_current_exception",
    "record_histogram",
    "trace_span",
]
