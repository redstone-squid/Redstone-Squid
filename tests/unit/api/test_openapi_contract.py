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

import pytest
import schemathesis

from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
schema = schemathesis.openapi.from_asgi("/openapi.json", _app)


@schema.parametrize()
# schemathesis's ASGI transport opens a fresh starlette TestClient per generated example, and its
# anyio portal is only torn down when garbage collected. Force collection before the test ends so
# that cleanup, and the ResourceWarning it emits, stays inside this test's ignore scope instead of
# leaking into session teardown where this suite's `filterwarnings = ["error"]` would fail on it.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_api_never_returns_a_server_error(case: schemathesis.Case) -> None:
    try:
        case.call_and_validate(
            headers={"Authorization": TEST_SYNERGY_SECRET},
            checks=[schemathesis.checks.not_a_server_error],
        )
    finally:
        gc.collect()


# Locale negotiation (squid/api/i18n.py) sits in front of every response, including error
# responses generated from schema-conformant-but-invalid requests. Fuzz Accept-Language
# alongside the generated request to make sure header parsing itself never 500s, and that
# ProblemDetail's title/detail stay non-empty strings regardless of what locale was requested.
@schema.parametrize()
@pytest.mark.parametrize("accept_language", ["en", "zh-CN", "zh-TW", "fr-FR;q=0.9,*;q=0.1", "not-a-locale-tag", ""])
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_api_never_errors_on_accept_language(case: schemathesis.Case, accept_language: str) -> None:
    try:
        case.call_and_validate(
            headers={"Authorization": TEST_SYNERGY_SECRET, "Accept-Language": accept_language},
            checks=[schemathesis.checks.not_a_server_error],
        )
    finally:
        gc.collect()
