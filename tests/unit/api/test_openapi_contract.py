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

import httpx
import pytest
import schemathesis
from hypothesis import HealthCheck, settings

from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
schema = schemathesis.openapi.from_asgi("/openapi.json", _app)


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
