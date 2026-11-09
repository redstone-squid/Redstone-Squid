"""Build reads batch related data so query cost stays bounded as the page grows."""

from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import IdentityProvider
from squid.accounts.models import Account, AccountIdentity, CreatorAlias
from squid.builds.domain import Status
from squid.builds.models import OtherBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.versions.models import Version
from tests.support.database import record_queries
from tests.support.unit_of_work import transact

# Keep the budget independent of page size; fixture data exercises related creator and version loads.
# Category columns load in one extra query per category on the page; the fixtures use only `OtherBuild`.
_EXPECTED_PAGE_QUERIES = 8


async def _seed(session_factory: async_sessionmaker[AsyncSession], count: int) -> tuple[int, list[int]]:
    """Create builds with distinct creators but a shared version and submitter."""
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(
            AccountIdentity(
                account_id=account.id,
                provider=IdentityProvider.DISCORD,
                subject="123456789",
            )
        )
        session.add(Version(edition="Java", major_version=1, minor_version=21, patch_number=0, data_version=3953))
        session.add_all(CreatorAlias(name=f"Builder {index}") for index in range(count))
        account_id = account.id

    repository = partial(SqlAlchemyUnitOfWork, session_factory)
    build_ids: list[int] = []
    for index in range(count):
        build = OtherBuild(
            submission_status=Status.PENDING,
            submitter_account_id=account_id,
            versions=["Java 1.21.0"],
            creators_ign=[f"Builder {index}"],
            ai_generated=False,
        )
        await transact(repository, lambda uow, build=build: uow.builds.save(build), commit=True)
        assert build.id is not None
        build_ids.append(build.id)
    return account_id, build_ids


@pytest.mark.parametrize("count", [1, 5])
async def test_list_page_query_count_does_not_grow_with_the_page(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    count: int,
) -> None:
    """Reading a page costs the same number of queries at any page size."""
    await _seed(migrated_session_factory, count)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    with record_queries(migrated_session_factory) as statements:
        builds = await transact(
            repository,
            lambda uow: uow.builds.list_page(
                statuses=frozenset({Status.PENDING}), submitter_account_id=None, after_id=None, limit=50
            ),
        )

    assert len(builds) == count
    assert len(statements) == _EXPECTED_PAGE_QUERIES, "\n".join(statements)


async def test_get_many_batches_cross_context_loads(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, build_ids = await _seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    with record_queries(migrated_session_factory) as statements:
        builds = await transact(repository, lambda uow: uow.builds.get_many(build_ids))

    assert [build.id for build in builds] == build_ids
    assert [build.creators_ign for build in builds] == [[f"Builder {index}"] for index in range(5)]
    assert all(build.versions == ["Java 1.21.0"] for build in builds)
    assert all(build.submitter_discord_id == 123456789 for build in builds)
    assert len(statements) == _EXPECTED_PAGE_QUERIES, "\n".join(statements)


async def test_get_builds_by_id_preserves_positions_including_duplicates(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Positional results survive batching, including repeats and misses."""
    _, build_ids = await _seed(migrated_session_factory, 2)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    requested = [build_ids[0], 999_999, build_ids[1], build_ids[0]]
    builds = await transact(repository, lambda uow: uow.builds.get_builds_by_id(requested))

    assert [None if build is None else build.id for build in builds] == [
        build_ids[0],
        None,
        build_ids[1],
        build_ids[0],
    ]
