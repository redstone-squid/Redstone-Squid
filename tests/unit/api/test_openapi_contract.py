"""OpenAPI contract fuzzing for the HTTP API.

Schemathesis generates schema-conformant requests straight from the FastAPI app's own OpenAPI
document and exercises the real ASGI app in-process, lifespan included. It targets a different bug
class than the coverage-guided Atheris harnesses under tests/fuzz/: those hit pure parsing
functions directly and can reach deep edge cases fast, but they can't reach anything that requires
a live request (routing, dependency injection, header/body coercion). Schemathesis can't reach
those deep parser edge cases the way Atheris does, but it can catch a handler crashing on a
structurally valid combination of headers, body, and query parameters that no one wrote a unit
test for.

Add every new route to this schema as the API grows; that's the point of wiring this up early.
"""

import gc
import json
from pathlib import Path

import httpx
import pytest
import schemathesis
from hypothesis import HealthCheck, settings

from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
schema = schemathesis.openapi.from_asgi("/openapi.json", _app)
OPENAPI_DOCUMENT = Path(__file__).resolve().parents[3] / "web" / "openapi.json"


@pytest.fixture
def collect_asgi_portals():
    """Collect Schemathesis's per-example TestClient portals after each fuzz test."""
    yield
    gc.collect()


@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
# schemathesis's ASGI transport opens a fresh starlette TestClient per generated example, and its
# anyio portal is only torn down when garbage collected. Force collection before the test ends so
# that cleanup, and the ResourceWarning it emits, stays inside this test's ignore scope instead of
# leaking into session teardown where this suite's `filterwarnings = ["error"]` would fail on it.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_api_never_returns_a_server_error(case: schemathesis.Case, collect_asgi_portals: None) -> None:
    case.call_and_validate(
        headers={"Authorization": TEST_SYNERGY_SECRET},
        checks=[schemathesis.checks.not_a_server_error],  # pyrefly: ignore  # pyright: ignore[reportAttributeAccessIssue]
    )


# Locale negotiation (squid/api/i18n.py) sits in front of every response, including error
# responses generated from schema-conformant-but-invalid requests. Fuzz Accept-Language
# alongside the generated request to make sure header parsing itself never 500s, and that
# ProblemDetail's title/detail stay non-empty strings regardless of what locale was requested.
def test_api_never_errors_on_accept_language(client: httpx.Client) -> None:
    for accept_language in ("en", "zh-CN", "zh-TW", "fr-FR;q=0.9,*;q=0.1", "not-a-locale-tag", ""):
        response = client.post(
            "/verify",
            json={"uuid": "not-a-uuid"},
            headers={"Authorization": TEST_SYNERGY_SECRET, "Accept-Language": accept_language},
        )
        assert response.status_code < 500
        problem = response.json()
        assert isinstance(problem["title"], str)
        assert problem["title"]
        assert isinstance(problem["detail"], str)
        assert problem["detail"]


def test_catalogue_extensions_are_registered_in_openapi() -> None:
    document = _app.openapi()
    schemas = document["components"]["schemas"]

    assert {"preview", "version_spec", "versions", "opening_time", "closing_time"} <= set(
        schemas["BuildSummary"]["properties"]
    )
    assert "key" in schemas["BuildTag"]["properties"]
    assert "holder_builds" in schemas["RecordDetail"]["properties"]
    assert "500" in document["paths"]["/v1/records/{record_id}"]["get"]["responses"]


def test_every_mutating_operation_accepts_an_idempotency_key() -> None:
    document = _app.openapi()
    streaming_retries = {("/v1/submissions/drafts/{draft_id}/media/{kind}", "post")}

    for path, path_item in document["paths"].items():
        for method in ("post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
            if (path, method) in streaming_retries:
                assert any(
                    parameter.get("in") == "query" and parameter.get("name") == "upload_id" for parameter in parameters
                ), f"{method.upper()} {path} lacks its streaming-safe retry UUID"
                continue
            assert any(
                parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key"
                for parameter in parameters
            ), f"{method.upper()} {path} does not declare Idempotency-Key"


def test_committed_openapi_document_matches_application() -> None:
    committed = json.loads(OPENAPI_DOCUMENT.read_text(encoding="utf-8"))

    assert committed == _app.openapi()
