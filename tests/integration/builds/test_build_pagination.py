"""Bidirectional build paging against live PostgreSQL.

A backward page is fetched in reversed display order and flipped back in memory, which is the kind
of thing that looks right in a fake and is wrong in SQL. These tests pin the ordering the flip
produces, and that a page's total agrees with what paging through it actually yields.
"""

from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.infrastructure.models import Account
from squid.builds.application.queries import BuildListSort
from squid.builds.domain import OtherBuild, Status
from squid.builds.infrastructure.repository import BuildRepository
from squid.core.pagination import FIRST_PAGE, PageSelector

pytestmark = pytest.mark.asyncio

_CONFIRMED = frozenset({Status.CONFIRMED})


async def seed(session_factory: async_sessionmaker[AsyncSession], count: int) -> list[int]:
    """Persist *count* confirmed builds and return their identifiers in creation order."""
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id

    repository = BuildRepository(session_factory)
    identifiers: list[int] = []
    for _ in range(count):
        build = OtherBuild(
            submission_status=Status.CONFIRMED,
            submitter_account_id=account_id,
            ai_generated=False,
        )
        await repository.save(build)
        assert build.id is not None
        identifiers.append(build.id)
    return identifiers


async def page_ids(
    repository: BuildRepository,
    *,
    selector: PageSelector = FIRST_PAGE,
    limit: int = 2,
) -> Sequence[int]:
    builds = await repository.list_page(
        statuses=_CONFIRMED,
        submitter_account_id=None,
        sort=BuildListSort(),
        offset=selector.offset,
        after_id=selector.after_id,
        before_id=selector.before_id,
        limit=limit,
    )
    return [build.id for build in builds if build.id is not None]


async def test_a_backward_page_comes_back_in_display_order(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 5)
    repository = BuildRepository(migrated_session_factory)
    newest_first = list(reversed(identifiers))

    forward = await page_ids(repository, selector=PageSelector(after_id=newest_first[1]))
    backward = await page_ids(repository, selector=PageSelector(before_id=newest_first[3]))

    assert list(forward) == newest_first[2:4]
    # Walking back from the fourth row returns the two rows above it, newest first, with the
    # overfetched row at the front rather than the tail.
    assert list(backward) == newest_first[1:3]


async def test_an_offset_page_and_a_keyset_page_agree(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 5)
    repository = BuildRepository(migrated_session_factory)
    newest_first = list(reversed(identifiers))

    by_offset = await page_ids(repository, selector=PageSelector(offset=2))
    by_anchor = await page_ids(repository, selector=PageSelector(after_id=newest_first[1]))

    assert list(by_offset) == list(by_anchor) == newest_first[2:4]


async def test_the_total_matches_what_paging_actually_returns(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(migrated_session_factory, 5)
    repository = BuildRepository(migrated_session_factory)

    total = await repository.count(statuses=_CONFIRMED, submitter_account_id=None)
    walked: list[int] = []
    selector = FIRST_PAGE
    while True:
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
