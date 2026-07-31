"""HTTP API transport tests."""

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_missing_authorization_header_returns_422(client: httpx.Client):
    resp = client.post("/verify", json={"uuid": str(TEST_UUID)})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM_DETAIL_MEDIA_TYPE)
    payload = resp.json()
    assert payload["title"] == "Invalid request"
    assert payload["code"] == ErrorCode.INVALID_REQUEST
    assert payload["context"]["errors"] == [
        {"location": ["header", "authorization"], "type": "missing", "message": "Field required"}
    ]
    assert "input" not in str(payload)


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
        response = internal_client.get("/boom")

    assert response.status_code == 500
    assert response.json()["detail"] == "An internal server error occurred."
    assert response.json()["code"] == ErrorCode.INTERNAL_ERROR
    assert "Sensitive" not in response.text
    assert "secret" not in response.text
    assert response.json()["error_id"] == response.headers["X-Error-ID"]


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
