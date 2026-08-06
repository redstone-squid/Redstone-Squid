"""HTTP API transport tests."""

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from squid.api import app as api_app
from squid.api.errors import PROBLEM_DETAIL_MEDIA_TYPE
from squid.core.errors import ErrorCode, InternalError
from squid.users.errors import MinecraftServiceUnavailableError
from tests.unit.api.fakes import (
    NONEXISTENT_UUID,
    TEST_SYNERGY_SECRET,
    TEST_UUID,
    TEST_VERIFICATION_CODE,
    MockDatabaseManager,
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
