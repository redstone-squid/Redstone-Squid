"""Tests for framework-free build application services."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest

from squid.builds.application.services import (
    BuildEditPatch,
    BuildService,
    DoorSubmissionInput,
    RestrictionDefinition,
)
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import BuildBusyError, BuildNotFoundError


class FakeBuildRepository:
    def __init__(self, build: Build | None = None) -> None:
        self.build = build
        self.save = AsyncMock()
        self.confirm = AsyncMock()
        self.deny = AsyncMock()
        self.acquire_lock = AsyncMock(return_value=True)
        self.release_lock = AsyncMock()
        self.clean_stale_locks = AsyncMock()

    async def get_by_id(self, build_id: int) -> Build | None:
        if self.build is not None and self.build.id == build_id:
            return self.build
        return None

    async def update_smallest_door_records_without_title(self) -> None:
        return None


class FakeRestrictionRepository:
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return [RestrictionDefinition("Seamless", "wiring-placement")]

    async def add_alias(self, restriction: str, alias: str) -> None:
        return None


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


def build_service(repository: FakeBuildRepository) -> BuildService:
    return BuildService(repository, FakeRestrictionRepository(), FakeVersions(), FakeEmbeddings())


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
    service = build_service(repository)
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
    repository.acquire_lock.assert_awaited_once_with(42, blocking=False, timeout=30)
    repository.release_lock.assert_awaited_once_with(42)


async def test_edit_commit_uses_repository_and_releases_after_save(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)

    async with service.edit(42, BuildEditPatch(door_dimensions=(2, 3, 4))) as lease:
        result = await lease.commit()

    assert result is existing_build
    assert existing_build.door_dimensions == (2, 3, 4)
    repository.save.assert_awaited_once_with(existing_build)
    repository.release_lock.assert_awaited_once_with(42)


async def test_edit_releases_lock_when_patch_application_fails(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)
    patch = BuildEditPatch(dimensions=(1, 2))  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValueError, match="not enough values to unpack"):
        async with service.edit(42, patch):
            pass

    repository.release_lock.assert_awaited_once_with(42)


async def test_edit_reports_missing_and_busy_builds(existing_build: Build) -> None:
    missing_service = build_service(FakeBuildRepository())
    with pytest.raises(BuildNotFoundError):
        async with missing_service.edit(42, BuildEditPatch()):
            pass

    busy_repository = FakeBuildRepository(existing_build)
    busy_repository.acquire_lock.return_value = False
    busy_service = build_service(busy_repository)
    with pytest.raises(BuildBusyError):
        async with busy_service.edit(42, BuildEditPatch()):
            pass


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
    service = BuildService(repository, FakeRestrictionRepository(), FakeVersions(), embeddings)
    build = Build(id=42)

    await service.save(build)

    assert build.versions == ["Java 1.21.0"]
    assert build.embedding == [1.0, 2.0]
    repository.save.assert_awaited_once_with(build)
    assert embeddings.prepared == [build]
    assert embeddings.indexed == [build]
