"""Schemathesis exploration of schema-conformant HTTP requests."""

import gc

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
# Schemathesis's ASGI transport opens a fresh Starlette TestClient per generated example, and its
# AnyIO portal is only torn down when garbage collected. Force collection before the test ends so
# that cleanup, and the ResourceWarning it emits, stays inside this test's ignore scope instead of
# leaking into session teardown where this suite's `filterwarnings = ["error"]` would fail on it.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_api_never_returns_a_server_error(case: schemathesis.Case, collect_asgi_portals: None) -> None:
    case.call_and_validate(
        headers={"Authorization": TEST_SYNERGY_SECRET},
        checks=[schemathesis.checks.not_a_server_error],  # pyrefly: ignore  # pyright: ignore[reportAttributeAccessIssue]
    )
