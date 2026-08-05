"""End-to-end framework span composition with the optional observability extra."""

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
from opentelemetry.sdk.trace import TracerProvider  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # pyright: ignore[reportMissingImports]
    InMemorySpanExporter,
)

from squid.observability import TraceContextFilter, correlation_id, trace_span


def test_http_request_contains_sql_child_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
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
    finally:
        FastAPIInstrumentor.uninstrument_app(api)
        SQLAlchemyInstrumentor().uninstrument()
        engine.dispose()
        provider.shutdown()

    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.name == "GET /probe" and span.kind.name == "SERVER")
    sql_span = next(span for span in spans if span.name.upper().startswith("SELECT"))
    correlation_span = next(span for span in spans if span.name == "GET /correlation" and span.kind.name == "SERVER")
    command_span = next(span for span in spans if span.name == "discord.command submit")
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
