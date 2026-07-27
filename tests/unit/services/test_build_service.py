"""Tests for framework-free build application services."""

from unittest.mock import AsyncMock

import pytest

from squid.db.builds import Build
from squid.db.schema import BuildCategory, Status
from squid.services.builds import (
    BuildBusyError,
    BuildEditPatch,
    BuildNotFoundError,
    BuildService,
    DoorSubmissionInput,
    RestrictionDefinition,
)


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

    async def update_smallest_door_records_without_title(self) -> None:
        return None


class FakeRestrictionRepository:
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return [RestrictionDefinition("Seamless", "wiring-placement")]

    async def add_alias(self, restriction: str, alias: str) -> None:
        return None


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
    existing_build.lock.acquire = AsyncMock(return_value=True)  # pyright: ignore[reportAttributeAccessIssue]
    existing_build.lock.release = AsyncMock()  # pyright: ignore[reportAttributeAccessIssue]
    service = BuildService(repository, FakeRestrictionRepository())
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
    existing_build.lock.release.assert_awaited_once()


async def test_edit_commit_uses_repository_and_releases_after_save(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    existing_build.lock.acquire = AsyncMock(return_value=True)  # pyright: ignore[reportAttributeAccessIssue]
    existing_build.lock.release = AsyncMock()  # pyright: ignore[reportAttributeAccessIssue]
    service = BuildService(repository, FakeRestrictionRepository())

    async with service.edit(42, BuildEditPatch(door_dimensions=(2, 3, 4))) as lease:
        result = await lease.commit()

    assert result is existing_build
    assert existing_build.door_dimensions == (2, 3, 4)
    repository.save.assert_awaited_once_with(existing_build)
    existing_build.lock.release.assert_awaited_once()


async def test_edit_releases_lock_when_patch_application_fails(existing_build: Build) -> None:
    repository = FakeBuildRepository(existing_build)
    existing_build.lock.acquire = AsyncMock(return_value=True)  # pyright: ignore[reportAttributeAccessIssue]
    existing_build.lock.release = AsyncMock()  # pyright: ignore[reportAttributeAccessIssue]
    service = BuildService(repository, FakeRestrictionRepository())
    patch = BuildEditPatch(dimensions=(1, 2))  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValueError, match="not enough values to unpack"):
        async with service.edit(42, patch):
            pass

    existing_build.lock.release.assert_awaited_once()


async def test_edit_reports_missing_and_busy_builds(existing_build: Build) -> None:
    missing_service = BuildService(FakeBuildRepository(), FakeRestrictionRepository())
    with pytest.raises(BuildNotFoundError):
        async with missing_service.edit(42, BuildEditPatch()):
            pass

    existing_build.lock.acquire = AsyncMock(return_value=False)  # pyright: ignore[reportAttributeAccessIssue]
    busy_service = BuildService(FakeBuildRepository(existing_build), FakeRestrictionRepository())
    with pytest.raises(BuildBusyError):
        async with busy_service.edit(42, BuildEditPatch()):
            pass


async def test_submit_door_maps_input_and_saves() -> None:
    repository = FakeBuildRepository()
    service = BuildService(repository, FakeRestrictionRepository())
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
