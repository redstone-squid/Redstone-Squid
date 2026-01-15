import uuid
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import squid.api as api_module
from squid.db import DatabaseManager

TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_NAME = "TestUser"
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"


class MockUserManager:
    async def generate_verification_code(self, user_uuid: str | UUID) -> int:
        if isinstance(user_uuid, str):
            user_uuid = uuid.UUID(user_uuid)
        if user_uuid == TEST_UUID:
            return TEST_VERIFICATION_CODE
        raise ValueError("User not found")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_environment(monkeypatch: pytest.MonkeyPatch, mock_db_manager: DatabaseManager):
    """Inject dummy DB + secret for every test."""

    # Provide the secret expected by the endpoint
    monkeypatch.setenv("SYNERGY_SECRET", TEST_SYNERGY_SECRET)

    # Swap the global `_db` used in the FastAPI router for a dummy
    mock_db_manager.user = MockUserManager()  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(api_module, "_db", mock_db_manager)

    return


@pytest.fixture
def client():
    with TestClient(api_module.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_missing_authorization_header_returns_422(client: httpx.Client):
    resp = client.post("/verify", json={"uuid": str(TEST_UUID)})
    assert resp.status_code == 422
    assert resp.json() == {
        "detail": [{"type": "missing", "loc": ["header", "authorization"], "msg": "Field required", "input": None}]
    }


def test_wrong_authorization_header_returns_401(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(TEST_UUID)},
        headers={"Authorization": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


def test_user_not_found_returns_400(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(NONEXISTENT_UUID)},
        headers={"Authorization": TEST_SYNERGY_SECRET},
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "User not found"}


def test_success_returns_verification_code(client: httpx.Client):
    resp = client.post(
        "/verify",
        json={"uuid": str(TEST_UUID)},
        headers={"Authorization": TEST_SYNERGY_SECRET},
    )
    assert resp.status_code == 201
    assert resp.json() == TEST_VERIFICATION_CODE
