"""HTTP API transport tests."""

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from squid.api import app as api_app
from squid.api.errors import PROBLEM_DETAIL_MEDIA_TYPE
from squid.auth.domain.sessions import WebSessionIdentity
from squid.builds.errors import BuildRevisionMismatchError, BuildRevisionRequiredError
from squid.core.errors import ErrorCode, InternalError
from squid.users.errors import MinecraftServiceUnavailableError
from tests.unit.api.fakes import (
    NONEXISTENT_UUID,
    TEST_SYNERGY_SECRET,
    TEST_UUID,
    TEST_VERIFICATION_CODE,
    MockDatabaseManager,
    build_app,
)


def test_main_owns_observability_shutdown(mocker: MockerFixture) -> None:
    config = mocker.Mock()
    config.api.port = 8123
    handle = mocker.Mock()
    mocker.patch.object(api_app, "configure_api_logging")
    configure = mocker.patch.object(api_app, "configure_observability", return_value=handle)
    run = mocker.patch("uvicorn.run")

    api_app.main(config)

    configure.assert_called_once_with(config.observability, service_name="api")
    run.assert_called_once()
    handle.shutdown.assert_called_once_with()


def test_create_app_delegates_optional_instrumentation(mocker: MockerFixture) -> None:
    config = mocker.Mock()
    instrument = mocker.patch.object(api_app, "instrument_api_app")

    api_app.create_api_app(config=config)

    instrument.assert_called_once_with(mocker.ANY, config.observability)


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
            user_id=1,
            discord_id=123,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout")

    assert database.closed
    assert response.status_code == 403
    web_auth.logout.assert_not_awaited()


def test_cookie_authenticated_write_rejects_mismatched_csrf_header(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            user_id=1,
            discord_id=123,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout", headers={"X-CSRF-Token": "wrong-token"})

    assert database.closed
    assert response.status_code == 403
    web_auth.logout.assert_not_awaited()


def test_cookie_authenticated_write_accepts_matching_csrf_header(mocker: MockerFixture) -> None:
    web_auth = mocker.Mock()
    web_auth.authenticate = mocker.AsyncMock(
        return_value=WebSessionIdentity(
            session_id="session",
            user_id=1,
            discord_id=123,
            consent_pending=False,
        )
    )
    web_auth.logout = mocker.AsyncMock()
    app, database = build_app(web_auth=web_auth)

    with TestClient(app, base_url="https://testserver") as session_client:
        session_client.cookies.set("__Host-squid_session", "session-token")
        session_client.cookies.set("squid_csrf", "csrf-token")
        response = session_client.post("/v1/auth/logout", headers={"X-CSRF-Token": "csrf-token"})

    assert database.closed
    assert response.status_code == 204
    web_auth.logout.assert_awaited_once_with("session-token")


async def test_verify_handler_depends_on_users_capability(mocker: MockerFixture) -> None:
    users = mocker.Mock()
    users.generate_verification_code = mocker.AsyncMock(return_value=TEST_VERIFICATION_CODE)

    result = await api_app.get_verification_code(
        api_app.User(uuid=TEST_UUID),
        users,
        mocker.Mock(),
    )

    assert result == TEST_VERIFICATION_CODE
    users.generate_verification_code.assert_awaited_once_with(TEST_UUID)


def test_internal_error_is_redacted_and_correlated(
    app_factory: tuple[FastAPI, MockDatabaseManager], mocker: MockerFixture
) -> None:
    app, _database = app_factory
    mocker.patch("squid.api.errors.correlation_id", return_value="a" * 32)

    @app.get("/boom")
    async def boom() -> None:
        raise InternalError(
            "Sensitive database detail.",
            context={"secret": "do-not-expose"},
            developer_action="Inspect the database.",
        )

    with TestClient(app, raise_server_exceptions=False) as internal_client:
        response = internal_client.get("/boom")

    assert response.status_code == 500
    assert response.json()["detail"] == "An internal server error occurred."
    assert response.json()["code"] == ErrorCode.INTERNAL_ERROR
    assert "Sensitive" not in response.text
    assert "secret" not in response.text
    assert response.json()["error_id"] == response.headers["X-Error-ID"]
    assert response.json()["error_id"] == "a" * 32


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
    assert response.json()["error_id"] == response.headers["X-Error-ID"]


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
