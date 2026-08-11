"""Build application service tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from whenever import Instant

from squid.builds.application import BuildEditPatch, DoorSubmissionInput, RestrictionDefinition
from squid.builds.application.services import (
    BuildService,
)
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import BuildBusyError, BuildNotFoundError, BuildRevisionMismatchError
from squid.core.errors import InvalidStateError
from squid.sponsors import PublicSponsor


class FakeBuildRepository:
    def __init__(self, build: Build | None = None) -> None:
        self.build = build
        self.save = AsyncMock()
        self.confirm = AsyncMock()
        self.deny = AsyncMock()

    async def get_by_id(self, build_id: int) -> Build | None:
        if self.build is not None and self.build.id == build_id:
            return self.build
        return None

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        if self.build is not None and self.build.source_submission_draft_id == draft_id:
            return self.build
        return None


class FakeRestrictionRepository:
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return [RestrictionDefinition("Seamless", "wiring-placement")]

    async def add_alias(self, restriction: str, alias: str) -> None:
        return None


class FakeBuildLocks:
    def __init__(self) -> None:
        self.acquire = AsyncMock(return_value=True)
        self.release = AsyncMock()
        self.clean_stale = AsyncMock()

    @asynccontextmanager
    async def locked(self, build_id: int, *, timeout: float = 30) -> AsyncIterator[None]:
        if not await self.acquire(build_id, blocking=True, timeout=timeout):
            raise BuildBusyError(build_id)
        try:
            yield
        finally:
            await self.release(build_id)


class FakeVersions:
    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str:
        return f"{edition} 1.21.0"


class FakeEmbeddings:
    def __init__(self) -> None:
        self.prepared: list[Build] = []
        self.indexed: list[Build] = []

    async def prepare(self, build: Build) -> None:
        self.prepared.append(build)
        build.embedding = [1.0, 2.0]

    async def index(self, build: Build) -> None:
        self.indexed.append(build)


def build_service(repository: FakeBuildRepository, locks: FakeBuildLocks | None = None) -> BuildService:
    return BuildService(
        repository,
        locks or FakeBuildLocks(),
        FakeRestrictionRepository(),
        FakeVersions(),
        FakeEmbeddings(),
    )


@pytest.fixture
def existing_build() -> Build:
    return Build(
        id=42,
        submitter_id=1,
        submission_status=Status.PENDING,
        category=BuildCategory.DOOR,
        miscellaneous_restrictions=["Locational", "Directional with fixes", "Other"],
        extra_info={
            "user": "old",
            "server_info": {"server_ip": "old.example", "coordinates": "1 2 3"},
        },
    )


async def test_edit_applies_only_after_lock_and_releases_on_cancel(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    patch = BuildEditPatch(
        dimensions=(10, 11, 12),
        locationality="Not locational",
        extra_user_info=None,
        server_ip="new.example",
        coordinates=None,
    )

    lease = service.edit(42, patch)
    assert existing_build.dimensions == (None, None, None)

    async with lease:
        assert lease.build.dimensions == (10, 11, 12)
        assert lease.build.miscellaneous_restrictions == ["Directional with fixes", "Other"]
        assert "user" not in lease.build.extra_info
        assert lease.build.extra_info.get("server_info") == {"server_ip": "new.example"}

    repository.save.assert_not_awaited()
    locks.acquire.assert_awaited_once_with(42, blocking=False, timeout=30)
    locks.release.assert_awaited_once_with(42)


async def test_edit_commit_uses_repository_and_releases_after_save(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)

    async with service.edit(42, BuildEditPatch(door_dimensions=(2, 3, 4))) as lease:
        result = await lease.commit()

    assert result is existing_build
    assert existing_build.door_dimensions == (2, 3, 4)
    repository.save.assert_awaited_once_with(existing_build)
    assert locks.acquire.await_args_list == [((42,), {"blocking": False, "timeout": 30})]
    assert locks.release.await_count == 1


async def test_edit_releases_lock_when_patch_application_fails(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    invalid_dimensions = cast(tuple[int | None, int | None, int | None], (1, 2))
    patch = BuildEditPatch(dimensions=invalid_dimensions)

    with pytest.raises(ValueError, match="not enough values to unpack"):
        async with service.edit(42, patch):
            pass

    locks.release.assert_awaited_once_with(42)


async def test_edit_reports_missing_and_busy_builds(existing_build: Build) -> None:
    missing_locks = FakeBuildLocks()
    missing_service = build_service(FakeBuildRepository(), missing_locks)
    with pytest.raises(BuildNotFoundError):
        async with missing_service.edit(42, BuildEditPatch()):
            pass
    missing_locks.release.assert_awaited_once_with(42)

    busy_locks = FakeBuildLocks()
    busy_locks.acquire.return_value = False
    busy_service = build_service(FakeBuildRepository(existing_build), busy_locks)
    with pytest.raises(BuildBusyError):
        async with busy_service.edit(42, BuildEditPatch()):
            pass


async def test_edit_loads_build_only_after_acquiring_lease(existing_build: Build) -> None:
    locks = FakeBuildLocks()
    repository = FakeBuildRepository(existing_build)
    original_get = repository.get_by_id

    async def get_after_lock(build_id: int) -> Build | None:
        locks.acquire.assert_awaited_once_with(42, blocking=False, timeout=30)
        return await original_get(build_id)

    repository.get_by_id = get_after_lock  # type: ignore[method-assign]

    async with build_service(repository, locks).edit(42, BuildEditPatch()):
        pass


async def test_edit_rejects_a_stale_expected_revision(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()

    with pytest.raises(BuildRevisionMismatchError) as error:
        async with build_service(repository, locks).edit(42, BuildEditPatch(dimensions=(1, 2, 3)), expected_revision=2):
            pass

    assert error.value.public_context == {"build_id": 42, "expected_revision": 2, "current_revision": 1}
    assert existing_build.dimensions == (None, None, None)
    locks.release.assert_awaited_once_with(42)


async def test_status_changes_and_cleanup_use_lock_manager(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    cutoff = Instant.from_utc(2026, 7, 29)

    await service.confirm(42)
    await service.deny(42)
    await service.clean_stale_locks(older_than=cutoff)

    repository.confirm.assert_awaited_once_with(existing_build)
    repository.deny.assert_awaited_once_with(existing_build)
    assert locks.acquire.await_count == 2
    assert locks.release.await_count == 2
    locks.clean_stale.assert_awaited_once_with(older_than=cutoff)


async def test_submit_door_maps_input_and_saves() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    submission = DoorSubmissionInput(
        submitter_id=123,
        door_size=(2, 3, None),
        build_size=(8, 9, 10),
        restrictions=("Seamless",),
        locationality="Locational",
        directionality="Not directional",
        information_about_build="notes",
    )

    build = await service.submit_door(submission)

    assert build.category is BuildCategory.DOOR
    assert build.submission_status is Status.PENDING
    assert build.submitter_id == 123
    assert build.door_dimensions == (2, 3, None)
    assert build.dimensions == (8, 9, 10)
    assert build.wiring_placement_restrictions == ["Seamless"]
    assert build.miscellaneous_restrictions == ["Locational"]
    assert build.extra_info.get("user") == "notes"
    repository.save.assert_awaited_once_with(build)


async def test_classify_restrictions_replaces_existing_values_without_persisting() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    build = Build(component_restrictions=["Old"], miscellaneous_restrictions=["Old"])

    result = await service.classify_restrictions(build, ["seamless", "unknown"])

    assert result is build
    assert build.wiring_placement_restrictions == ["Seamless"]
    assert build.component_restrictions == []
    assert build.miscellaneous_restrictions == []
    repository.save.assert_not_awaited()


async def test_submit_for_account_does_not_require_a_discord_identity() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    draft_id = UUID("11111111-1111-1111-1111-111111111111")

    build = await service.submit_for_account(
        Build(description="A transport-neutral submission"),
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        display_name="  Workshop prototype  ",
        ai_generated=False,
        category=BuildCategory.OTHER,
    )

    assert build.submitter_account_id == 17
    assert build.submitter_id is None
    assert build.source_submission_draft_id == draft_id
    assert build.display_name == "Workshop prototype"
    assert build.category is BuildCategory.OTHER
    assert build.submission_status is Status.PENDING
    repository.save.assert_awaited_once_with(build)


async def test_get_by_source_submission_draft_id_returns_an_existing_build() -> None:
    draft_id = UUID("11111111-1111-4111-8111-111111111111")
    existing = Build(id=41, source_submission_draft_id=draft_id)

    result = await build_service(FakeBuildRepository(existing)).get_by_source_submission_draft_id(draft_id)

    assert result is existing


async def test_submit_for_account_returns_the_build_created_by_an_earlier_retry() -> None:
    draft_id = UUID("22222222-2222-2222-2222-222222222222")
    existing = Build(
        id=42,
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        category=BuildCategory.UTILITY,
        submission_status=Status.PENDING,
    )
    repository = FakeBuildRepository(existing)

    result = await build_service(repository).submit_for_account(
        Build(),
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        display_name="ignored retry value",
        ai_generated=False,
        category=BuildCategory.UTILITY,
    )

    assert result is existing
    repository.save.assert_not_awaited()


async def test_submit_for_account_rejects_existing_build_with_different_sponsor() -> None:
    draft_id = UUID("22222222-2222-4222-8222-222222222223")
    installation_id = UUID("33333333-3333-4333-8333-333333333333")
    existing = Build(
        id=42,
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        category=BuildCategory.UTILITY,
        submission_status=Status.PENDING,
        sponsor=PublicSponsor(installation_id, display_name="Original server"),
    )
    repository = FakeBuildRepository(existing)

    with pytest.raises(InvalidStateError):
        await build_service(repository).submit_for_account(
            Build(sponsor=PublicSponsor(installation_id, display_name="Changed server")),
            submitter_account_id=17,
            source_submission_draft_id=draft_id,
            display_name="Retry",
            ai_generated=False,
            category=BuildCategory.UTILITY,
        )

    repository.save.assert_not_awaited()


async def test_save_prepares_defaults_then_indexes_after_relational_persistence() -> None:
    repository = FakeBuildRepository()
    embeddings = FakeEmbeddings()
    locks = FakeBuildLocks()
    service = BuildService(repository, locks, FakeRestrictionRepository(), FakeVersions(), embeddings)
    build = Build(id=42)

    await service.save(build)

    assert build.versions == ["Java 1.21.0"]
    assert build.embedding == [1.0, 2.0]
    repository.save.assert_awaited_once_with(build)
    locks.acquire.assert_awaited_once_with(42, blocking=True, timeout=30)
    locks.release.assert_awaited_once_with(42)
    assert embeddings.prepared == [build]
    assert embeddings.indexed == [build]
