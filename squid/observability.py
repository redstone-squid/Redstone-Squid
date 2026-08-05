"""Process-local OpenTelemetry initialization with an optional runtime dependency."""

import logging
import os
import threading
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

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


def _configure_otel(config: ObservabilityConfig, *, service_name: str) -> ObservabilityHandle:
    """Build the SDK pipeline after the caller has established process ownership."""
    from opentelemetry import trace  # pyright: ignore[reportMissingImports]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
        OTLPSpanExporter,
    )
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
    return ObservabilityHandle(provider.shutdown)


def _trace_endpoint(endpoint: str) -> str:
    """Resolve the generic OTLP HTTP base URL to the traces signal endpoint."""
    parsed = urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    path = base_path if base_path.endswith("/v1/traces") else f"{base_path}/v1/traces"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


__all__ = ["ObservabilityHandle", "configure_observability"]
