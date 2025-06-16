"""Unit tests for the FastAPI verification endpoint."""

import os
import uuid
from typing import Callable, Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from squid.api import User, app, get_verification_code
from squid.db import DatabaseManager

# Create a test client
client = TestClient(app)

# Sample test data
VALID_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
VALID_USERNAME = "TestUser"
INVALID_SECRET = "wrong_secret"


@pytest.fixture
def mock_get_username() -> Generator[AsyncMock, None]:
    """Mock get_minecraft_username function."""
    with patch("squid.api.get_minecraft_username", new_callable=AsyncMock) as mock:
        mock.return_value = VALID_USERNAME
        yield mock


@pytest.mark.asyncio
async def test_valid_verification_request(
    mock_env_vars: None, mock_db_manager: DatabaseManager, mock_get_username: Callable[[str | uuid.UUID], str | None]
):
    """Test successful verification code generation."""
    response = client.post(
        "/verify", json={"uuid": str(VALID_UUID)}, headers={"Authorization": os.getenv("SYNERGY_SECRET")}
    )

    assert response.status_code == 201
    assert isinstance(response.json(), int)
    assert 100000 <= response.json() <= 999999
    mock_get_username.assert_called_once_with(VALID_UUID)


@pytest.mark.asyncio
async def test_unauthorized_request(mock_env_vars: None):
    """Test request with invalid authorization."""
    response = client.post("/verify", json={"uuid": str(VALID_UUID)}, headers={"Authorization": INVALID_SECRET})

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


@pytest.mark.asyncio
async def test_invalid_user(mock_env_vars: None, mock_get_username: Callable[[str | uuid.UUID], str | None]):
    """Test request with invalid user UUID."""
    mock_get_username.return_value = None

    response = client.post(
        "/verify", json={"uuid": str(VALID_UUID)}, headers={"Authorization": os.getenv("SYNERGY_SECRET")}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid user"


@pytest.mark.asyncio
async def test_invalid_uuid_format(mock_env_vars: None):
    """Test request with malformed UUID."""
    response = client.post(
        "/verify", json={"uuid": "not-a-uuid"}, headers={"Authorization": os.getenv("SYNERGY_SECRET")}
    )

    assert response.status_code == 422  # FastAPI validation error


@pytest.mark.asyncio
async def test_missing_authorization_header():
    """Test request without authorization header."""
    response = client.post("/verify", json={"uuid": str(VALID_UUID)})

    assert response.status_code == 422  # FastAPI validation error


@pytest.mark.asyncio
async def test_verification_code_range(
    mock_env_vars: None, mock_db_manager: DatabaseManager, mock_get_username: Callable[[str | uuid.UUID], str | None]
):
    """Test that generated verification codes are within expected range."""
    response = client.post(
        "/verify", json={"uuid": str(VALID_UUID)}, headers={"Authorization": os.getenv("SYNERGY_SECRET")}
    )
    assert response.status_code == 201

    code = response.json()
    assert 100000 <= code <= 999999  # 6-digit code
    assert isinstance(code, int)
