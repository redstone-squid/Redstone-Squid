"""Version catalog transactions against PostgreSQL."""

from functools import partial

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import InvalidStateError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.versions.application.services import VersionService
from squid.versions.domain import MinecraftVersion
from squid.versions.models import Version


async def test_version_service_commits_and_lists_versions_in_numeric_order(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = VersionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    newer = await service.add("99.10.0")
    older = await service.add("99.2.0")
    bedrock = await service.add("99.3.0", edition="Bedrock")

    assert (await service.list_versions("Java"))[-2:] == [older, newer]
    assert await service.newest("Java") == "Java 99.10.0"
    assert bedrock in await service.list_all()
    assert bedrock not in await service.list_versions("Java")
    assert await service.resolve_spec("99.2 - 99.10") == ["Java 99.2.0", "Java 99.10.0"]

    # The schema stores version components as small integers; commit must fail
    # before the service returns a value that was never persisted.
    with pytest.raises(DBAPIError):
        await service.add("40000.0.0")
    assert await service.newest("Java") == "Java 99.10.0"


async def test_uncommitted_versions_roll_back_after_autoflush(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    version = MinecraftVersion("Java", 99, 1, 0)
    async with factory() as uow:
        repository = uow.versions
        await repository.add(version)
        assert version in await repository.list("Java")

    async with factory() as uow:
        assert version not in await uow.versions.list("Java")
    with pytest.raises(InvalidStateError):
        await repository.add(version)


async def test_data_version_lookups_use_shared_catalogue(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory() as session:
        session.add_all(
            [
                Version(edition="Java", major_version=99, minor_version=2, patch_number=0, data_version=99002),
                Version(edition="Java", major_version=99, minor_version=1, patch_number=0, data_version=99002),
                Version(edition="Bedrock", major_version=99, minor_version=1, patch_number=0),
            ]
        )
        await session.commit()

    service = VersionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    assert await service.data_version_for("Java 99.2.0") == 99002
    assert await service.label_for_data_version(99002) == "Java 99.1.0"
    assert await service.data_version_for("Bedrock 99.1.0") is None
    assert await service.data_version_for("Java 99.99.0") is None
    assert await service.data_version_for("not a version") is None
    assert await service.label_for_data_version(99999) is None
