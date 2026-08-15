"""Squid's telemetry surface, exercised against a live OpenTelemetry SDK.

Not a test of the third-party instrumentors. Every assertion here is about Squid code
whose behaviour only exists once composed with them, and which nothing else can check:
`correlation_id` resolving to the trace an operator can search for, `TraceContextFilter`
stamping that trace onto log records, and `inject_trace_context`/`extracted_trace_span`
carrying a trace across the schematic process boundary. Unit tests of those functions can
only assert against a mocked tracer, which is how they would keep passing after the
composition broke.

The one genuinely third-party claim - a SQL span nesting under the HTTP server span -
earns its place because the two instrumentors are configured independently by Squid, and
a flat trace is exactly what a misconfiguration produces.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # pyright: ignore[reportMissingImports]
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # pyright: ignore[reportMissingImports]
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.metrics import MeterProvider  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.metrics.export import (  # pyright: ignore[reportMissingImports]
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)
from opentelemetry.sdk.trace import TracerProvider  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # pyright: ignore[reportMissingImports]
    InMemorySpanExporter,
)

from squid import observability
from squid.observability import (
    TraceContextFilter,
    correlation_id,
    extracted_trace_span,
    inject_trace_context,
    trace_span,
)


def test_squid_telemetry_composes_with_a_live_sdk() -> None:
    """One setup, five claims: nesting, correlation id, log stamping, propagation, privacy.

    Kept as a single test because instrumenting FastAPI and SQLAlchemy and standing up a
    provider is the expensive part; splitting it would repeat that four more times to
    assert against the same trace.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    previous_active_pid = observability._telemetry_active_pid  # pyright: ignore[reportPrivateUsage]
    observability._telemetry_active_pid = observability.os.getpid()  # pyright: ignore[reportPrivateUsage]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    api = FastAPI()
    FastAPIInstrumentor.instrument_app(api, tracer_provider=provider)
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)

    @api.get("/probe")
    def probe() -> dict[str, int]:
        with engine.connect() as connection:
            return {"value": connection.execute(text("select 1")).scalar_one()}

    @api.get("/correlation")
    def correlation() -> dict[str, str]:
        return {"error_id": correlation_id()}

    try:
        with TestClient(api) as client:
            assert client.get("/probe").json() == {"value": 1}
            traced_error_id = client.get("/correlation").json()["error_id"]
        command_record = logging.LogRecord("squid.bot", logging.INFO, __file__, 1, "command", (), None)
        with trace_span(
            "discord.command submit",
            {"squid.command.name": "submit", "squid.surface": "application_command"},
        ):
            TraceContextFilter().filter(command_record)
        tracer = trace.get_tracer("propagation-test")
        carrier: dict[str, object] = {}
        with tracer.start_as_current_span("schematic.supervisor"):
            inject_trace_context(carrier)
        with extracted_trace_span(
            "schematic.worker analyze",
            carrier,
            {"squid.schematic.operation": "analyze"},
        ):
            pass
    finally:
        FastAPIInstrumentor.uninstrument_app(api)
        SQLAlchemyInstrumentor().uninstrument()
        engine.dispose()
        provider.shutdown()
        observability._telemetry_active_pid = previous_active_pid  # pyright: ignore[reportPrivateUsage]

    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.name == "GET /probe" and span.kind.name == "SERVER")
    sql_span = next(span for span in spans if span.name.upper().startswith("SELECT"))
    correlation_span = next(span for span in spans if span.name == "GET /correlation" and span.kind.name == "SERVER")
    command_span = next(span for span in spans if span.name == "discord.command submit")
    supervisor_span = next(span for span in spans if span.name == "schematic.supervisor")
    worker_span = next(span for span in spans if span.name == "schematic.worker analyze")
    assert sql_span.context is not None
    assert server_span.context is not None
    assert correlation_span.context is not None
    assert sql_span.context.trace_id == server_span.context.trace_id
    assert sql_span.parent is not None
    assert sql_span.parent.span_id == server_span.context.span_id
    assert traced_error_id == f"{correlation_span.context.trace_id:032x}"
    assert command_span.context is not None
    assert vars(command_record)["trace_id"] == f"{command_span.context.trace_id:032x}"
    assert vars(command_record)["span_id"] == f"{command_span.context.span_id:016x}"
    assert command_span.attributes is not None
    assert "squid.user.id" not in command_span.attributes
    traceparent = carrier["traceparent"]
    assert isinstance(traceparent, str)
    assert traceparent.startswith("00-")
    assert worker_span.parent is not None
    assert supervisor_span.context is not None
    assert worker_span.parent.span_id == supervisor_span.context.span_id


def test_worker_metrics_are_recorded_by_the_optional_sdk() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=(reader,))
    previous_meter = observability._meter  # pyright: ignore[reportPrivateUsage]
    previous_counters = observability._counters  # pyright: ignore[reportPrivateUsage]
    previous_histograms = observability._histograms  # pyright: ignore[reportPrivateUsage]
    observability._meter = provider.get_meter("squid.test")  # pyright: ignore[reportPrivateUsage]
    observability._counters = {}  # pyright: ignore[reportPrivateUsage]
    observability._histograms = {}  # pyright: ignore[reportPrivateUsage]
    try:
        observability.add_counter(
            "squid.schematic.worker.crashes",
            attributes={"squid.worker.exit_code": -9, "squid.worker.failure_reason": "crash"},
        )
        observability.record_histogram(
            "squid.schematic.operation.duration",
            0.25,
            attributes={"squid.schematic.operation": "analyze", "squid.outcome": "error"},
        )
        metrics_data = reader.get_metrics_data()
    finally:
        provider.shutdown()
        observability._meter = previous_meter  # pyright: ignore[reportPrivateUsage]
        observability._counters = previous_counters  # pyright: ignore[reportPrivateUsage]
        observability._histograms = previous_histograms  # pyright: ignore[reportPrivateUsage]

    assert metrics_data is not None
    metrics = {
        metric.name: metric
        for resource_metrics in metrics_data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    crash_point = metrics["squid.schematic.worker.crashes"].data.data_points[0]
    duration_point = metrics["squid.schematic.operation.duration"].data.data_points[0]
    assert isinstance(crash_point, NumberDataPoint)
    assert isinstance(duration_point, HistogramDataPoint)
    assert crash_point.attributes == {"squid.worker.exit_code": -9, "squid.worker.failure_reason": "crash"}
    assert crash_point.value == 1
    assert duration_point.attributes == {"squid.schematic.operation": "analyze", "squid.outcome": "error"}
    assert duration_point.count == 1
