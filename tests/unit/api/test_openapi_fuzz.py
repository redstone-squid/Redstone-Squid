"""Schemathesis exploration of schema-conformant HTTP requests."""

import gc

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
schema = schemathesis.openapi.from_asgi("/openapi.json", _app)


@schemathesis.serializer("image/*", "video/*")
def serialize_upload_body(_context: schemathesis.SerializationContext, value: object) -> bytes:
    """Send generated upload bodies verbatim.

    Schemathesis ships no serializer for the wildcard media types the draft media upload
    accepts, and an unserializable body is reported as a test failure rather than a skip.
    """
    if isinstance(value, bytes):
        return value
    data = getattr(value, "data", None)
    return data if isinstance(data, bytes) else str(value).encode()


@pytest.fixture
def collect_asgi_portals():
    """Collect Schemathesis's per-example TestClient portals after each fuzz test."""
    yield
    gc.collect()


@schema.parametrize()
@settings(
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        # Flaky otherwise, and for a reason that has nothing to do with what this asserts.
        # Operations whose fields carry a narrow pattern plus a long minimum -- the worst is
        # `PaperChallengeExchangeRequest.device_code`, `^[A-Za-z0-9_-]+$` with `minLength: 32`
        # -- have most generated strings filtered out, and whether that crosses the health
        # check's threshold depends on the seed, so it fails under some test orderings and not
        # others. Generation efficiency does not affect the property here: every example that
        # does survive still has to come back as something other than a 5xx.
        HealthCheck.filter_too_much,
    ]
)
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
