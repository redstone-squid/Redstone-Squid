"""Shared fakes and test configuration for HTTP API transport tests."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI

from squid.api.app import create_api_app
from squid.config import ApiProcessConfig
from squid.runtime import ApplicationRuntime, ApplicationServices
from squid.users.errors import MinecraftAccountNotFoundError

TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"
TEST_CONFIG = ApiProcessConfig.model_validate(
    {
        "database": {"url": "postgresql://user:password@database.example/squid"},
        "verification": {"code_pepper": "verification-pepper"},
        "api": {"secret": TEST_SYNERGY_SECRET},
    }
)


class MockUserManager:
    async def generate_verification_code(self, user_uuid: str | UUID) -> int:
        if isinstance(user_uuid, str):
            user_uuid = uuid.UUID(user_uuid)
        if user_uuid == TEST_UUID:
            return TEST_VERIFICATION_CODE
        raise MinecraftAccountNotFoundError(user_uuid)


class MockDatabaseManager:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def build_app() -> tuple[FastAPI, MockDatabaseManager]:
    """Build the API app wired to in-memory fakes instead of real infrastructure."""
    database = MockDatabaseManager()
    services = cast(ApplicationServices, SimpleNamespace(users=MockUserManager()))
    runtime = ApplicationRuntime(services, database.close, AsyncMock())
    return create_api_app(lambda _config: runtime, config=TEST_CONFIG), database
