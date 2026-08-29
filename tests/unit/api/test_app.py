"""HTTP API transport tests."""

import re

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from squid.accounts.errors import MinecraftServiceUnavailableError
from squid.api import app as api_app
from squid.api.errors import PROBLEM_DETAIL_MEDIA_TYPE
from squid.auth.domain.sessions import WebSessionIdentity
from squid.builds.errors import BuildRevisionMismatchError, BuildRevisionRequiredError
from squid.core.errors import ErrorCode, InternalError
from tests.unit.api.fakes import (
    NONEXISTENT_UUID,
    TEST_CONFIG,
    TEST_SYNERGY_SECRET,
    TEST_UUID,
    TEST_VERIFICATION_CODE,
    MockDatabaseManager,
    MockErrorReports,
    build_app,
)


def test_main_owns_observability_shutdown(mocker: MockerFixture) -> None:
    config = mocker.Mock()
    config.api.port = 8123
    config.api.trusted_proxy_ips = ("127.0.0.1", "10.0.0.0/8")
    handle = mocker.Mock()
    mocker.patch.object(api_app, "configure_api_logging")
    configure = mocker.patch.object(api_app, "configure_observability", return_value=handle)
    run = mocker.patch("uvicorn.run")

    api_app.main(config)

    configure.assert_called_once_with(config.observability, service_name="api")
    run.assert_called_once()
    assert run.call_args.kwargs["proxy_headers"] is True
    assert run.call_args.kwargs["forwarded_allow_ips"] == ["127.0.0.1", "10.0.0.0/8"]
    handle.shutdown.assert_called_once_with()


def test_liveness_and_readiness_have_distinct_endpoints(client: httpx.Client) -> None:
    assert client.get("/livez").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/health").json() == {"status": "ready"}


def test_missing_authorization_header_returns_401(client: httpx.Client):
    resp = client.post("/verify", json={"uuid": str(TEST_UUID)})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_DETAIL_MEDIA_TYPE)
    payload = resp.json()
    assert payload["title"] == "Unauthorized"
    assert payload["code"] == ErrorCode.UNAUTHORIZED


def test_wrong_authorization_header_returns_401(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(TEST_UUID)},
        headers={"Authorization": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_DETAIL_MEDIA_TYPE)
    assert resp.json()["code"] == ErrorCode.UNAUTHORIZED
    assert resp.json()["detail"] == "Unauthorized."


def test_user_not_found_returns_400(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(NONEXISTENT_UUID)},
        headers={"Authorization": TEST_SYNERGY_SECRET},
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_DETAIL_MEDIA_TYPE)
    assert resp.json()["code"] == ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    assert resp.json()["context"] == {"minecraft_uuid": str(NONEXISTENT_UUID)}


def test_success_returns_verification_code(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(TEST_UUID)},
        headers={"Authorization": TEST_SYNERGY_SECRET},
    )
    assert resp.status_code == 201
    assert resp.json() == TEST_VERIFICATION_CODE


def test_cookie_authenticated_write_requires_csrf_header(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            account_id=1,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    idempotency = mocker.Mock()
    idempotency.reserve = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth, idempotency=idempotency)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout", headers={"Idempotency-Key": "logout-request"})

    assert database.closed
    assert response.status_code == 403
    idempotency.reserve.assert_not_awaited()
    web_auth.logout.assert_not_awaited()


def test_cookie_authenticated_write_rejects_mismatched_csrf_header(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            account_id=1,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout", headers={"CSRF-Token": "wrong-token"})

    assert database.closed
    assert response.status_code == 403
    web_auth.logout.assert_not_awaited()


def test_cookie_authenticated_write_accepts_matching_csrf_header(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            account_id=1,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout", headers={"CSRF-Token": "csrf-token"})

    assert database.closed
    assert response.status_code == 204
    web_auth.logout.assert_awaited_once_with("session-token")


def test_cookie_authenticated_frontend_can_fetch_its_no_store_csrf_token(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            account_id=1,
            consent_pending=False,
        )
    )
    config = TEST_CONFIG.model_copy(
        update={"api": TEST_CONFIG.api.model_copy(update={"cors_origins": ("https://catalogue.test",)})}
    )
    app, database = build_app(web_auth=web_auth, config=config)

    with TestClient(app, base_url="https://api.catalogue.test") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token-with-enough-entropy")
        response = session_client.get("/v1/auth/csrf", headers={"Origin": "https://catalogue.test"})

    assert database.closed
    assert response.status_code == 200
    assert response.json() == {"csrf_token": "csrf-token-with-enough-entropy"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Access-Control-Allow-Origin"] == "https://catalogue.test"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_anonymous_frontend_cannot_fetch_a_csrf_token(client: httpx.Client) -> None:
    response = client.get("/v1/auth/csrf", headers={"Origin": "https://catalogue.test"})

    assert response.status_code == 401
    assert "csrf_token" not in response.text


async def test_verify_handler_depends_on_accounts_capability(mocker: MockerFixture) -> None:
    accounts = mocker.Mock()
    accounts.generate_verification_code = mocker.AsyncMock(return_value=TEST_VERIFICATION_CODE)

    result = await api_app.get_verification_code(
        api_app.User(uuid=TEST_UUID),
        accounts,
        mocker.Mock(),
    )

    assert result == TEST_VERIFICATION_CODE
    accounts.generate_verification_code.assert_awaited_once_with(TEST_UUID)


def test_internal_error_is_redacted_and_correlated(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory

    @app.get("/boom")
    async def boom() -> None:
        raise InternalError(
            "Sensitive database detail.",
            context={"secret": "do-not-expose"},
            developer_action="Inspect the database.",
        )

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        response = internal_client.get("/boom", headers={"Request-Id": "a" * 32})

    assert response.status_code == 500
    assert response.json()["detail"] == "An internal server error occurred."
    assert response.json()["code"] == ErrorCode.INTERNAL_ERROR
    assert "Sensitive" not in response.text
    assert "secret" not in response.text
    assert "error_id" not in response.json()
    assert response.headers["Request-Id"] == "a" * 32


def test_service_unavailable_is_safe_and_correlated(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory

    @app.get("/unavailable")
    async def unavailable() -> None:
        raise MinecraftServiceUnavailableError(
            "Mojang returned sensitive diagnostics.",
            context={"secret": "do-not-expose"},
        )

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        response = internal_client.get("/unavailable")

    assert response.status_code == 503
    assert response.json()["detail"] == "A required service is temporarily unavailable. Please try again later."
    assert response.json()["code"] == ErrorCode.MINECRAFT_SERVICE_UNAVAILABLE
    assert response.json()["resource"] == "minecraft_account"
    assert "sensitive" not in response.text
    assert "secret" not in response.text
    assert "error_id" not in response.json()
    assert re.fullmatch(r"[A-Za-z0-9._-]{8,128}", response.headers["Request-Id"])


def test_unhandled_exception_still_carries_request_id(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    """A bare exception renders in ServerErrorMiddleware, outside the correlation middleware.

    The 500 response must still carry a Request-Id -- proving the exception path's explicit
    header emission and the middleware's skip-unbind-on-raise both hold.
    """
    app, _database = app_factory

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        response = internal_client.get("/unhandled", headers={"Request-Id": "b" * 32})

    assert response.status_code == 500
    assert "error_id" not in response.json()
    assert response.headers["Request-Id"] == "b" * 32


def test_failures_are_captured_under_the_request_id_the_caller_sees() -> None:
    """The Request-Id echoed back is the key the stored report is filed under.

    Without that agreement a caller quoting the header they received would resolve nothing, which
    is the whole point of storing the report.
    """
    reports = MockErrorReports()
    app, _database = build_app(error_reports=reports)

    @app.get("/boom")
    async def boom() -> None:
        raise InternalError("Sensitive database detail.")

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        internal_client.get("/boom", headers={"Request-Id": "c" * 32})
        internal_client.get("/unhandled", headers={"Request-Id": "d" * 32})

    assert [call["correlation_id"] for call in reports.calls] == ["c" * 32, "d" * 32]
    assert [call["reference"] for call in reports.calls] == ["c" * 12, "d" * 12]
    assert {call["surface"] for call in reports.calls} == {"http"}
    assert [call["origin"] for call in reports.calls] == ["GET /boom", "GET /unhandled"]


def test_a_validation_failure_is_not_captured() -> None:
    """A 422 is the caller's mistake, fully explained in the response, and not a stored incident."""
    reports = MockErrorReports()
    app, _database = build_app(error_reports=reports)

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        response = internal_client.get("/v1/builds", params={"page_size": "not-a-number"})

    assert response.status_code == 422
    assert reports.calls == []


def test_build_revision_errors_use_http_preconditions(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory

    @app.get("/revision-required")
    async def revision_required() -> None:
        raise BuildRevisionRequiredError(42)

    @app.get("/revision-mismatch")
    async def revision_mismatch() -> None:
        raise BuildRevisionMismatchError(42, expected_revision=3, current_revision=4)

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        required = internal_client.get("/revision-required")
        mismatch = internal_client.get("/revision-mismatch")

    assert required.status_code == 428
    assert required.json()["code"] == ErrorCode.BUILD_REVISION_REQUIRED
    assert mismatch.status_code == 412
    assert mismatch.json()["context"] == {"build_id": 42, "expected_revision": 3, "current_revision": 4}
