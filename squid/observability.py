"""Configure and shut down the optional OpenTelemetry SDK for one process."""

import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit, urlunsplit

from squid.core.observability import Meter, Span, Telemetry, Tracer, install_trace_context_log_filter

if TYPE_CHECKING:
    from squid.config import ObservabilityConfig

logger = logging.getLogger(__name__)


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


def configure_observability(config: ObservabilityConfig, *, service_name: str) -> ObservabilityHandle:
    """Configure OTLP trace and metric export once in the current process.

    Disabled deployments do not import OpenTelemetry. Missing optional dependencies
    produce a warning and a no-op handle. Later calls return the same handle, even
    after shutdown. Configure each process after forking; inherited configuration
    raises RuntimeError.

    The configured service name is the deployment prefix; `service_name` identifies
    the process component. The caller owns the returned shutdown handle.
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

    active = Telemetry(
        pid=os.getpid(),
        tracer=cast(Tracer, trace.get_tracer("squid")),
        worker_tracer=cast(Tracer, trace.get_tracer("squid.worker")),
        meter=cast(Meter, metric_provider.get_meter("squid")),
        propagator=propagate,
        current_span=cast(Callable[[], Span], trace.get_current_span),
        error_status=lambda: Status(StatusCode.ERROR),
    )
    # The instrumentor's version bound excludes 2.1; the live-SDK integration test covers our usage.
    SQLAlchemyInstrumentor().instrument(skip_dep_check=True)
    AioHttpClientInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    install_trace_context_log_filter()
    active.activate()

    def shutdown() -> None:
        active.deactivate()
        try:
            provider.shutdown()
        finally:
            metric_provider.shutdown()

    return ObservabilityHandle(shutdown)


def _resource_attributes(config: ObservabilityConfig, component: str) -> dict[str, str]:
    """Build low-cardinality deployment identity shared by traces and metrics."""
    attributes = {
        "service.name": f"{config.service_name}-{component}",
        "deployment.environment.name": config.environment,
    }
    if config.release is not None:
        attributes["service.version"] = config.release
    return attributes


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """Resolve the generic OTLP HTTP base URL to one signal endpoint."""
    parsed = urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    signal_path = f"/v1/{signal}"
    path = base_path if base_path.endswith(signal_path) else f"{base_path}{signal_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
