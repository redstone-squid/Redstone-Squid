"""Build application service tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast
from unittest.mock import AsyncMock

import pytest
from whenever import Instant

from squid.builds.application import BuildEditPatch, DoorSubmissionInput, RestrictionDefinition
from squid.builds.application.services import (
    BuildService,
)
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import BuildBusyError, BuildNotFoundError


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
    assert locks.acquire.await_args_list == [
        ((42,), {"blocking": False, "timeout": 30}),
        ((42,), {"blocking": True, "timeout": 30}),
    ]
    assert locks.release.await_count == 2


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
    missing_service = build_service(FakeBuildRepository())
    with pytest.raises(BuildNotFoundError):
        async with missing_service.edit(42, BuildEditPatch()):
            pass

    busy_locks = FakeBuildLocks()
    busy_locks.acquire.return_value = False
    busy_service = build_service(FakeBuildRepository(existing_build), busy_locks)
    with pytest.raises(BuildBusyError):
        async with busy_service.edit(42, BuildEditPatch()):
            pass


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
