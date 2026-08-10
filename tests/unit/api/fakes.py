"""Shared fakes and test configuration for HTTP API transport tests."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI

from squid.accounts.errors import MinecraftAccountNotFoundError
from squid.api.app import create_api_app
from squid.config import ApiProcessConfig
from squid.idempotency import PendingRequest
from squid.notifications import NotificationPreferences
from squid.runtime import ApiServices, ApplicationRuntime
from squid.schematics.errors import SchematicNotFoundError
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.domain import SearchPage

TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"
TEST_CONFIG = ApiProcessConfig.model_validate(
    {
        "database": {"url": "postgresql://user:password@database.example/squid"},
        "verification": {"code_pepper": "verification-pepper"},
        "cursor": {"secret": "cursor-secret-for-tests"},
        "api": {
            "secret": TEST_SYNERGY_SECRET,
            "key_pepper": "api-key-pepper-for-tests",
            "session_pepper": "session-pepper-for-tests",
        },
    }
)


class MockAccountManager:
    async def get_creator_alias(self, _name: str):
        return None

    async def get_creator_profile(self, _public_id: UUID):
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

    async def suggest(self, _query: str, *, limit: int = 5) -> tuple[str, ...]:
        return ()

    async def fields(self):
        return DEFAULT_FIELD_REGISTRY


class MockAuthorization:
    async def is_global_administrator(self, _discord_id: int) -> bool:
        return False


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

    async def render_content(self, _recipe_hash: str):
        raise SchematicNotFoundError


class MockVotes:
    async def get_session_by_id(self, _vote_session_id: int):
        return None


class MockRecords:
    async def get(self, _result_id: int):
        return None

    async def list_page(self, **_kwargs: object):
        return ()


class MockIdempotency:
    async def reserve(self, **_kwargs: object) -> PendingRequest:
        return PendingRequest(uuid.uuid4())

    async def complete(self, *_args: object, **_kwargs: object) -> None:
        return None


class MockNotifications:
    async def preferences(self, account_id: int):
        return NotificationPreferences(account_id=account_id, notice_version=None, consented_at=None)

    async def accept_notice(self, account_id: int, *, web_enabled: bool, dm_enabled: bool):
        return NotificationPreferences(
            account_id=account_id,
            notice_version="2026-08-10",
            consented_at=None,
            web_enabled=web_enabled,
            dm_enabled=dm_enabled,
        )

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool):
        return NotificationPreferences(
            account_id=account_id,
            notice_version="2026-08-10",
            consented_at=None,
            web_enabled=web_enabled,
            dm_enabled=dm_enabled,
        )

    async def subscriptions(self, _account_id: int):
        return ()

    async def subscribe(self, _account_id: int, **_kwargs: object):
        raise AssertionError("service principals cannot create notification subscriptions")

    async def unsubscribe(self, _account_id: int, _subscription_id: int) -> None:
        return None

    async def can_view_staff(self, _discord_id: int) -> bool:
        return False

    async def inbox(self, _account_id: int, **_kwargs: object):
        return ()

    async def mark_read(self, _account_id: int, _notification_id: int, **_kwargs: object) -> None:
        return None


def build_app(
    *,
    web_auth: object | None = None,
    idempotency: object | None = None,
    accounts: object | None = None,
    config: ApiProcessConfig = TEST_CONFIG,
) -> tuple[FastAPI, MockDatabaseManager]:
    """Build the API app wired to in-memory fakes instead of real infrastructure."""
    database = MockDatabaseManager()
    services = cast(
        ApiServices,
        SimpleNamespace(
            api_keys=None,
            web_auth=web_auth,
            idempotency=idempotency or MockIdempotency(),
            notifications=MockNotifications(),
            builds=SimpleNamespace(),
            accounts=accounts or MockAccountManager(),
            build_queries=MockBuildQueries(),
            authorization=MockAuthorization(),
            search=MockSearch(),
            tags=MockTags(),
            versions=MockVersions(),
            schematics=MockSchematics(),
            votes=MockVotes(),
            vote_members=None,
            records=MockRecords(),
        ),
    )
    runtime = ApplicationRuntime(services, database.close, AsyncMock())
    return create_api_app(lambda _config: runtime, config=config), database
