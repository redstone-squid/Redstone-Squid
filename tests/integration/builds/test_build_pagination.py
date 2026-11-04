"""Bidirectional build paging preserves display order and agrees with count and offset queries."""

from collections.abc import Sequence
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.builds.application.queries import BuildListSort
from squid.builds.domain import Status
from squid.builds.models import OtherBuild
from squid.core.pagination import FIRST_PAGE, PageSelector
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

pytestmark = pytest.mark.asyncio

_CONFIRMED = frozenset({Status.CONFIRMED})


async def seed(session_factory: async_sessionmaker[AsyncSession], count: int) -> list[int]:
    """Persist *count* confirmed builds and return their identifiers in creation order."""
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id

    repository = partial(SqlAlchemyUnitOfWork, session_factory)
    identifiers: list[int] = []
    for _ in range(count):
        build = OtherBuild(
            submission_status=Status.CONFIRMED,
            submitter_account_id=account_id,
            ai_generated=False,
        )
        await transact(repository, lambda uow, build=build: uow.builds.save(build), commit=True)
        assert build.id is not None
        identifiers.append(build.id)
    return identifiers


async def page_ids(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    *,
    selector: PageSelector = FIRST_PAGE,
    limit: int = 2,
) -> Sequence[int]:
    builds = await transact(
        repository,
        lambda uow: uow.builds.list_page(
            statuses=_CONFIRMED,
            submitter_account_id=None,
            sort=BuildListSort(),
            offset=selector.offset,
            after_id=selector.after_id,
            before_id=selector.before_id,
            limit=limit,
        ),
    )
    return [build.id for build in builds if build.id is not None]


async def test_a_backward_page_comes_back_in_display_order(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    newest_first = list(reversed(identifiers))

    forward = await page_ids(repository, selector=PageSelector(after_id=newest_first[1]))
    backward = await page_ids(repository, selector=PageSelector(before_id=newest_first[3]))

    assert list(forward) == newest_first[2:4]
    # Walking back from the fourth row returns the preceding two rows, newest first.
    assert list(backward) == newest_first[1:3]


async def test_an_offset_page_and_a_keyset_page_agree(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    newest_first = list(reversed(identifiers))

    by_offset = await page_ids(repository, selector=PageSelector(offset=2))
    by_anchor = await page_ids(repository, selector=PageSelector(after_id=newest_first[1]))

    assert list(by_offset) == list(by_anchor) == newest_first[2:4]


async def test_the_total_matches_what_paging_actually_returns(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    total = await transact(repository, lambda uow: uow.builds.count(statuses=_CONFIRMED, submitter_account_id=None))
    walked: list[int] = []
    selector = FIRST_PAGE
    while True:
        # The third row is a sentinel; display two and advance from the last displayed row.
        rows = list(await page_ids(repository, selector=selector, limit=3))
        if not rows:
            break
        walked.extend(rows[:2])
        if len(rows) <= 2:
            break
        selector = PageSelector(after_id=walked[-1])

    assert total == 5
    assert len(walked) == total
    assert len(set(walked)) == total
