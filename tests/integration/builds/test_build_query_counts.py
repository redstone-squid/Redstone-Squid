"""Query-count coverage for the build read paths.

`BuildMapper` loads the values other contexts own with its own SELECTs rather
than traversing ORM relationships, which cross-context decoupling deliberately
removed. That made mapping a page O(rows) queries. These tests pin the fixed
cost so the batching cannot silently regress into an N+1.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity, CreatorAlias
from squid.builds.domain import OtherBuild, Status
from squid.builds.infrastructure.repository import BuildRepository
from squid.versions.infrastructure.models import Version

# Row query + tag-assignment and tag-definition eager loads + links eager load,
# then the creator, submitter-identity, version and source-message batches.
_EXPECTED_PAGE_QUERIES = 7


@contextmanager
def _counting(session_factory: async_sessionmaker[AsyncSession]) -> Iterator[list[str]]:
    """Record every statement executed on the factory's engine."""
    statements: list[str] = []
    engine = session_factory.kw["bind"].sync_engine

    def before_cursor_execute(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


async def _seed(session_factory: async_sessionmaker[AsyncSession], count: int) -> tuple[int, list[int]]:
    """Create *count* builds, each with its own creator, version and submitter."""
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

    repository = BuildRepository(session_factory)
    build_ids: list[int] = []
    for index in range(count):
        build = OtherBuild(
            submission_status=Status.PENDING,
            submitter_account_id=account_id,
            versions=["Java 1.21.0"],
            creators_ign=[f"Builder {index}"],
            ai_generated=False,
        )
        await repository.save(build)
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
    repository = BuildRepository(migrated_session_factory)

    with _counting(migrated_session_factory) as statements:
        builds = await repository.list_page(
            statuses=frozenset({Status.PENDING}),
            submitter_id=None,
            submitter_account_id=None,
            after_id=None,
            limit=50,
        )

    assert len(builds) == count
    # One row query, two eager loads (tag assignments and their definitions are
    # one selectin each... links is the second), and the mapper's cross-context
    # batches for creators, submitter identities, versions and source messages.
    # Source messages cost one query whether or not the page has any, because
    # provenance is a link table now rather than a column on the build row. The
    # point is that none of these scale with the page.
    assert len(statements) == _EXPECTED_PAGE_QUERIES, "\n".join(statements)


async def test_get_many_batches_cross_context_loads(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, build_ids = await _seed(migrated_session_factory, 5)
    repository = BuildRepository(migrated_session_factory)

    with _counting(migrated_session_factory) as statements:
        builds = await repository.get_many(build_ids)

    assert [build.id for build in builds] == build_ids
    assert [build.creators_ign for build in builds] == [[f"Builder {index}"] for index in range(5)]
    assert all(build.versions == ["Java 1.21.0"] for build in builds)
    assert all(build.submitter_id == 123456789 for build in builds)
    assert len(statements) == _EXPECTED_PAGE_QUERIES, "\n".join(statements)


async def test_get_builds_by_id_preserves_positions_including_duplicates(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Positional results survive batching, including repeats and misses."""
    _, build_ids = await _seed(migrated_session_factory, 2)
    repository = BuildRepository(migrated_session_factory)

    requested = [build_ids[0], 999_999, build_ids[1], build_ids[0]]
    builds = await repository.get_builds_by_id(requested)

    assert [None if build is None else build.id for build in builds] == [
        build_ids[0],
        None,
        build_ids[1],
        build_ids[0],
    ]
