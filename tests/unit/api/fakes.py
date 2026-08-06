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
from squid.schematics.errors import SchematicNotFoundError
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.domain import SearchPage
from squid.users.errors import MinecraftAccountNotFoundError

TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"
TEST_CONFIG = ApiProcessConfig.model_validate(
    {
        "database": {"url": "postgresql://user:password@database.example/squid"},
        "verification": {"code_pepper": "verification-pepper"},
        "cursor": {"secret": "cursor-secret-for-tests"},
        "api": {"secret": TEST_SYNERGY_SECRET, "key_pepper": "api-key-pepper-for-tests"},
    }
)


class MockUserManager:
    async def get_creator_alias(self, _name: str):
        return None

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


class MockBuildQueries:
    async def get(self, _build_id: int):
        return None

    async def get_many(self, _build_ids: list[int]):
        return []

    async def list_page(self, **_kwargs: object):
        return []


class MockSearch:
    async def search(self, _request: object) -> SearchPage:
        return SearchPage(hits=(), next_cursor=None, has_more=False)

    async def fields(self):
        return DEFAULT_FIELD_REGISTRY


class MockTags:
    async def public_definitions(self):
        return ()

    async def public_definition(self, _tag_id: int):
        return None


class MockVersions:
    async def list_all(self):
        return []


class MockSchematics:
    async def list_for_build(self, _build_id: int):
        return []

    async def content(self, _sha256: str):
        raise SchematicNotFoundError


class MockVotes:
    async def get_session_by_id(self, _vote_session_id: int):
        return None


class MockRecords:
    async def get(self, _result_id: int):
        return None

    async def list_page(self, **_kwargs: object):
        return ()


def build_app() -> tuple[FastAPI, MockDatabaseManager]:
    """Build the API app wired to in-memory fakes instead of real infrastructure."""
    database = MockDatabaseManager()
    services = cast(
        ApplicationServices,
        SimpleNamespace(
            api_keys=None,
            users=MockUserManager(),
            build_queries=MockBuildQueries(),
            search=MockSearch(),
            tags=MockTags(),
            versions=MockVersions(),
            schematics=MockSchematics(),
            votes=MockVotes(),
            records=MockRecords(),
        ),
    )
    runtime = ApplicationRuntime(services, database.close, AsyncMock())
    return create_api_app(lambda _config: runtime, config=TEST_CONFIG), database
