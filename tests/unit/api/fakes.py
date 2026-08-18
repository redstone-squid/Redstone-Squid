"""Shared fakes and test configuration for HTTP API transport tests."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI

from squid.accounts.domain import AccountProfile
from squid.accounts.errors import MinecraftAccountNotFoundError
from squid.api.app import create_api_app
from squid.builds.errors import BuildNotFoundError
from squid.cli_auth.errors import InvalidCliEnrollmentError
from squid.config import ApiProcessConfig
from squid.core.pagination import Page
from squid.idempotency import PendingRequest
from squid.media.application.jobs import StagedMediaUploadSubmission
from squid.media.domain import MediaLimits
from squid.minecraft_auth.errors import InvalidChallengeError, InvalidInstallationCredentialError
from squid.notifications import NotificationPreferences
from squid.permissions.application import PermissionService, SubjectRecords
from squid.permissions.domain import Pattern
from squid.runtime import ApiServices, ApplicationRuntime
from squid.schematics.errors import SchematicNotFoundError
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.domain import SearchPage
from squid.submissions.application import FormOptionSet, build_submission_manifest
from squid.suggestions.application import SuggestionRegistry, SuggestionService


def credential_nodes(*raw: str) -> frozenset[Pattern]:
    """Build the parsed patterns a credential carries, as the real boundaries do."""
    return frozenset(Pattern.parse(pattern) for pattern in raw)


TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"
TEST_CONFIG = ApiProcessConfig.model_validate(
    {
        "database": {"url": "postgresql://user:password@database.example/squid"},
        "verification": {"code_pepper": "verification-pepper"},
        "api": {
            "secret": TEST_SYNERGY_SECRET,
            "key_pepper": "api-key-pepper-for-tests",
            "session_pepper": "session-pepper-for-tests",
            "idempotency_active_key_id": "test-v1",
            "idempotency_keys": {"test-v1": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="},
            # The bootstrap secret no longer carries every node by default, so a
            # deployment that still relays verifications with it has to say so.
            "secret_nodes": ["account.verify.relay"],
        },
        "cli_auth": {
            "pepper": "cli-authorization-pepper-for-tests-32-bytes",
            "verification_uri": "https://catalogue.example/cli/link",
        },
        # Configured so the device-flow routes reach their handlers: an unconfigured
        # verification URI answers 503, which is indistinguishable from a real fault.
        "minecraft_auth": {
            "pepper": "minecraft-authorization-pepper-for-tests",
            "verification_uri": "https://catalogue.example/minecraft/link",
        },
    }
)


class MockAccountManager:
    async def get_creator_alias(self, _name: str):
        return None

    async def get_creator_profile(self, _public_id: UUID):
        return None

    async def get_public_profile(self, _public_id: UUID):
        return None

    async def get_profile(self, account_id: int):
        return AccountProfile.empty(account_id)

    async def list_identities(self, _account_id: int):
        return ()

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


class MockCliAuthorization:
    """Fail closed with a client-safe error for generated contract requests."""

    def __getattr__(self, _name: str):
        async def unavailable(*_args: object, **_kwargs: object):
            raise InvalidCliEnrollmentError

        return unavailable


_EMPTY_PAGE: Page[object] = Page(items=(), total=0, next=None, prev=None)
"""What a paginated query returns when nothing matches.

The routes hand this straight to `render_page`, so a bare `()` or `[]` here reads as an
empty result but reaches production code as the wrong type and 500s.
"""


class MockBuildQueries:
    async def get(self, _build_id: int):
        return None

    async def get_public(self, build_id: int):
        raise BuildNotFoundError(build_id)

    async def get_many(self, _build_ids: list[int]):
        return []

    async def list_page(self, **_kwargs: object) -> Page[object]:
        return _EMPTY_PAGE


class MockSearch:
    async def search(self, _request: object) -> SearchPage:
        return SearchPage(hits=(), total=0, next=None, prev=None)

    async def suggest(self, _query: str, *, limit: int = 5) -> tuple[str, ...]:
        return ()

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


class MockErrorReports:
    """Records what the exception handlers captured, without a database."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, error: BaseException, **kwargs: object) -> None:
        self.calls.append({"error": error, **kwargs})


class MockSchematics:
    async def list_for_build(self, _build_id: int):
        return []

    async def content(self, _sha256: str):
        raise SchematicNotFoundError

    async def render_content(self, _recipe_hash: str):
        raise SchematicNotFoundError


class MockMinecraftInstallations:
    """Fail closed with a client-safe error for generated contract requests."""

    def __getattr__(self, _name: str):
        async def unauthenticated(*_args: object, **_kwargs: object):
            raise InvalidInstallationCredentialError

        return unauthenticated


class MockMinecraftPlayerAuthorization:
    """Fail closed with a client-safe error for generated contract requests."""

    def __getattr__(self, _name: str):
        async def invalid(*_args: object, **_kwargs: object):
            raise InvalidChallengeError

        return invalid


class MockMediaJobs:
    """An empty media store: every lookup misses, which the routes report as a 404."""

    limits = MediaLimits()

    async def submit_staged(self, submission: StagedMediaUploadSubmission) -> UUID:
        return submission.upload_id or uuid.uuid4()

    async def get(self, _upload_id: UUID):
        return None

    async def list_for_draft(self, _draft_id: UUID):
        return ()

    async def discard(self, _draft_id: UUID, _upload_id: UUID) -> bool:
        return False


class MockVotes:
    async def get_session_by_id(self, _vote_session_id: int):
        return None


class MockRecords:
    async def get(self, _result_id: int):
        return None

    async def list_page(self, **_kwargs: object) -> Page[object]:
        return _EMPTY_PAGE


class MockSubmissionForms:
    def manifest(self, *, locale: str | None):
        return build_submission_manifest(locale)

    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None):
        manifest = build_submission_manifest(locale)
        if manifest.schema_id == schema_id and manifest.revision == revision:
            return manifest
        return None

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        del locale
        return FormOptionSet(source, category, 1, ())


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
        raise AssertionError("service callers cannot create notification subscriptions")

    async def unsubscribe(self, _account_id: int, _subscription_id: int) -> None:
        return None

    async def inbox(self, _account_id: int, **_kwargs: object):
        return ()

    async def mark_read(self, _account_id: int, _notification_id: int, **_kwargs: object) -> None:
        return None


class MockPermissions:
    """A permission service over an empty store, so nodes fall to their defaults."""

    def __init__(self) -> None:
        self._service = PermissionService(EmptyPermissionStore())

    def __getattr__(self, name: str) -> object:
        return getattr(self._service, name)


class EmptyPermissionStore:
    """No stored rules, and a fixed epoch."""

    async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
        return SubjectRecords(epoch=1)

    async def epoch(self) -> int:
        return 1


class MockPermissionEpoch:
    """A watcher with nothing to watch, so the lifespan's job is a no-op."""

    listener = None

    async def refresh(self) -> None:
        return None


def build_app(
    *,
    web_auth: object | None = None,
    cli_authorization: object | None = None,
    idempotency: object | None = None,
    accounts: object | None = None,
    error_reports: object | None = None,
    config: ApiProcessConfig = TEST_CONFIG,
) -> tuple[FastAPI, MockDatabaseManager]:
    """Build the API app wired to in-memory fakes instead of real infrastructure."""
    database = MockDatabaseManager()
    services = cast(
        ApiServices,
        SimpleNamespace(
            api_keys=None,
            web_auth=web_auth,
            cli_authorization=cli_authorization or MockCliAuthorization(),
            idempotency=idempotency or MockIdempotency(),
            notifications=MockNotifications(),
            builds=SimpleNamespace(),
            accounts=accounts or MockAccountManager(),
            build_queries=MockBuildQueries(),
            permissions=MockPermissions(),
            permission_epoch=MockPermissionEpoch(),
            search=MockSearch(),
            # The real service over an empty registry, not a mock: every source id is
            # then unknown, which is the 404 the route promises rather than a 500.
            suggestions=SuggestionService(SuggestionRegistry.of(())),
            tags=MockTags(),
            versions=MockVersions(),
            schematics=MockSchematics(),
            votes=MockVotes(),
            vote_members=None,
            records=MockRecords(),
            submission_forms=MockSubmissionForms(),
            submission_drafts=SimpleNamespace(),
            submission_finalization=SimpleNamespace(),
            media_jobs=MockMediaJobs(),
            minecraft_installations=MockMinecraftInstallations(),
            minecraft_player_authorization=MockMinecraftPlayerAuthorization(),
            error_reports=error_reports or MockErrorReports(),
        ),
    )
    runtime = ApplicationRuntime(services, database.close, AsyncMock())
    return create_api_app(lambda _config: runtime, config=config), database
