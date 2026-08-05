"""End-to-end framework span composition with the optional observability extra."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # pyright: ignore[reportMissingImports]
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace import TracerProvider  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # pyright: ignore[reportMissingImports]
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # pyright: ignore[reportMissingImports]
    InMemorySpanExporter,
)

from squid.observability import correlation_id


def test_http_request_contains_sql_child_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
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
    finally:
        FastAPIInstrumentor.uninstrument_app(api)
        SQLAlchemyInstrumentor().uninstrument()
        engine.dispose()
        provider.shutdown()

    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.name == "GET /probe" and span.kind.name == "SERVER")
    sql_span = next(span for span in spans if span.name.upper().startswith("SELECT"))
    correlation_span = next(span for span in spans if span.name == "GET /correlation" and span.kind.name == "SERVER")
    assert sql_span.context is not None
    assert server_span.context is not None
    assert correlation_span.context is not None
    assert sql_span.context.trace_id == server_span.context.trace_id
    assert sql_span.parent is not None
    assert sql_span.parent.span_id == server_span.context.span_id
    assert traced_error_id == f"{correlation_span.context.trace_id:032x}"
