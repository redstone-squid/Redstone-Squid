"""Version application service tests."""

from functools import partial

import pytest

from squid.versions.application.services import VersionService
from squid.versions.domain import Edition, MinecraftVersion
from squid.versions.errors import VersionCatalogUnavailableError
from tests.support.unit_of_work import FakeUnitOfWork


class FakeVersionRepository:
    def __init__(self) -> None:
        self.versions: list[MinecraftVersion] = []

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        self.versions.append(version)
        return version

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        return [version for version in self.versions if version.edition == edition]


class FakeVersionUnitOfWork(FakeUnitOfWork):
    def __init__(self, committed: FakeVersionRepository) -> None:
        self._committed = committed
        self._versions = FakeVersionRepository()
        self._versions.versions = committed.versions.copy()

    @property
    def versions(self) -> FakeVersionRepository:
        return self._versions

    async def commit(self) -> None:
        self._committed.versions = self.versions.versions.copy()


async def test_version_service_honors_explicit_edition() -> None:
    repository = FakeVersionRepository()
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, repository))

    version = await service.add("1.21.4", edition="Bedrock")

    assert version == MinecraftVersion("Bedrock", 1, 21, 4)
    assert repository.versions == [version]


async def test_version_service_lists_both_editions() -> None:
    repository = FakeVersionRepository()
    repository.versions = [MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50)]

    assert (
        await VersionService(uow_factory=partial(FakeVersionUnitOfWork, repository)).list_all() == repository.versions
    )


async def test_version_service_resolves_ranges_and_newest_version() -> None:
    repository = FakeVersionRepository()
    repository.versions = [
        MinecraftVersion("Java", 1, 19, 0),
        MinecraftVersion("Java", 1, 19, 1),
        MinecraftVersion("Java", 1, 20, 0),
        MinecraftVersion("Java", 1, 21, 0),
    ]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, repository))

    assert await service.newest("Java") == "Java 1.21.0"
    assert await service.resolve_spec("1.19 - 1.20, 1.21+") == [
        "Java 1.19.0",
        "Java 1.19.1",
        "Java 1.20.0",
        "Java 1.21.0",
    ]


async def test_version_service_reports_an_empty_catalog_as_infrastructure_failure() -> None:
    repository = FakeVersionRepository()
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, repository))

    with pytest.raises(VersionCatalogUnavailableError) as exc_info:
        await service.newest("Bedrock")

    assert exc_info.value.context == {"edition": "Bedrock"}
