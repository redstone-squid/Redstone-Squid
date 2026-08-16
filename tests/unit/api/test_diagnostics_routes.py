"""Stored error report routes and the node that gates them."""

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from whenever import Instant

from squid.core.errors import ErrorCode
from squid.diagnostics.domain import ErrorReport, ErrorReportNotFoundError
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app

OCCURRED = Instant.from_utc(2026, 8, 17, 12, 0, 0)


def make_report(reference: str = "0a1b2c3d4e5f") -> ErrorReport:
    return ErrorReport(
        id=uuid4(),
        correlation_id=reference + "0" * 20,
        reference=reference,
        occurred_at=OCCURRED,
        expires_at=OCCURRED.add(hours=168),
        surface="application_command",
        origin="records lookup",
        exception_type="RuntimeError",
        message="the connection string is postgres://secret",
        error_code=ErrorCode.INTERNAL_ERROR,
        traceback="Traceback (most recent call last):\n  RuntimeError: boom\n",
        context={"channel_id": 4},
        log_tail=["fetching record 17"],
    )


class StubErrorReports:
    def __init__(self, report: ErrorReport | None = None, *, matches: int = 1) -> None:
        self._report = report
        self._matches = matches

    async def lookup(self, reference: str) -> tuple[ErrorReport, int]:
        if self._report is None:
            raise ErrorReportNotFoundError(context={"reference": reference})
        return self._report, self._matches

    async def recent(self, *, limit: int) -> list[ErrorReport]:
        return [self._report] if self._report is not None else []


# The bootstrap secret carries only the nodes a deployment names, so reading reports has to be
# granted explicitly -- which is also what proves the route is gated on the node rather than on
# merely being authenticated.
DIAGNOSTIC_CONFIG = TEST_CONFIG.model_copy(
    update={"api": TEST_CONFIG.api.model_copy(update={"secret_nodes": ["diagnostics.error.read"]})}
)

AUTHORIZED = {"Authorization": TEST_SYNERGY_SECRET}


@pytest.fixture
def client_for() -> Any:
    def build(reports: StubErrorReports, *, config: Any = DIAGNOSTIC_CONFIG) -> TestClient:
        app, _database = build_app(error_reports=cast(Any, reports), config=config)
        return TestClient(app, raise_server_exceptions=False)

    return build


def test_lookup_returns_the_internals_the_user_was_never_shown(client_for: Any) -> None:
    report = make_report()

    with client_for(StubErrorReports(report)) as client:
        response = client.get(f"/v1/diagnostics/errors/{report.reference}", headers=AUTHORIZED)

    assert response.status_code == 200
    body = response.json()
    assert body["reference"] == report.reference
    assert body["correlation_id"] == report.correlation_id
    assert body["message"] == "the connection string is postgres://secret"
    assert body["traceback"].startswith("Traceback")
    assert body["log_tail"] == ["fetching record 17"]
    assert body["matching_references"] == 1


def test_an_ambiguous_reference_says_so(client_for: Any) -> None:
    """The short reference is a 48-bit prefix, not a key; a reader must be told."""
    with client_for(StubErrorReports(make_report(), matches=2)) as client:
        response = client.get("/v1/diagnostics/errors/0a1b2c3d4e5f", headers=AUTHORIZED)

    assert response.json()["matching_references"] == 2


def test_an_unknown_reference_is_a_problem_document(client_for: Any) -> None:
    with client_for(StubErrorReports(None)) as client:
        response = client.get("/v1/diagnostics/errors/ffffffffffff", headers=AUTHORIZED)

    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.NOT_FOUND


def test_an_anonymous_caller_is_refused(client_for: Any) -> None:
    """Tracebacks name internal paths and unredacted messages; the node is the only way in."""
    with client_for(StubErrorReports(make_report())) as client:
        detail = client.get("/v1/diagnostics/errors/0a1b2c3d4e5f")
        listing = client.get("/v1/diagnostics/errors")

    assert detail.status_code == 401
    assert listing.status_code == 401


def test_listing_summarises_without_the_traceback(client_for: Any) -> None:
    """A listing is for finding an incident, not for reading one."""
    with client_for(StubErrorReports(make_report())) as client:
        response = client.get("/v1/diagnostics/errors", headers=AUTHORIZED)

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["reference"] == "0a1b2c3d4e5f"
    assert "traceback" not in item
    assert "message" not in item


def test_an_over_long_reference_is_rejected_before_the_service(client_for: Any) -> None:
    reports = StubErrorReports(make_report())

    with client_for(reports) as client:
        response = client.get(f"/v1/diagnostics/errors/{'a' * 200}", headers=AUTHORIZED)

    assert response.status_code == 422
