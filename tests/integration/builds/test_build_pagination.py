"""Bidirectional build paging preserves display order and agrees with count and offset queries."""

from collections.abc import Sequence
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.builds.application.queries import BuildListSort
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import OtherBuild
from squid.core.pagination import FIRST_PAGE, PageAnchorNotFoundError, PageSelector
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.unit_of_work import UnitOfWorkFactory
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact

pytestmark = pytest.mark.asyncio

_CONFIRMED = frozenset({BuildStatus.APPROVED})


async def seed(session_factory: async_sessionmaker[AsyncSession], count: int) -> list[int]:
    """Persist *count* confirmed builds and return their identifiers in creation order."""
    async with build_edit_session(session_factory) as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id

    repository = partial(SqlAlchemyUnitOfWork, session_factory)
    identifiers: list[int] = []
    for _ in range(count):
        build = OtherBuild(
            submission_status=BuildStatus.APPROVED,
            submitter_account_id=account_id,
            ai_generated=False,
        )
        await transact(
            repository,
            lambda uow, build=build: uow.builds.save(build),
            commit=True,
        )
        assert build.id is not None
        identifiers.append(build.id)
    return identifiers


async def page_ids(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    *,
    selector: PageSelector = FIRST_PAGE,
    limit: int = 2,
    sort: BuildListSort = BuildListSort.ID,
) -> Sequence[int]:
    builds = await transact(
        repository,
        lambda uow: uow.builds.list_page(
            statuses=_CONFIRMED,
            submitter_account_id=None,
            sort=sort,
            offset=selector.offset,
            after_id=selector.after_id,
            before_id=selector.before_id,
            limit=limit,
        ),
    )
    # ORM identity columns may still be None before flush.
    return [build.id for build in builds if build.id is not None]  # pyright: ignore[reportUnnecessaryComparison]


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


async def set_widths(
    session_factory: async_sessionmaker[AsyncSession], identifiers: Sequence[int], widths: Sequence[int | None]
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow, uow.builds.editing(actor="test", source="test"):
        for build_id, width in zip(identifiers, widths, strict=True):
            build = await uow.builds.get_for_update(build_id)
            assert build is not None
            build.width, build.height, build.depth = width, 2, 4
        await uow.commit()


async def test_volume_order_places_tied_builds_before_unknown_dimensions(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 4)
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await set_widths(migrated_session_factory, identifiers, (3, None, 2, 2))
    async with factory() as uow:
        ordered = await uow.builds.list_page(statuses=_CONFIRMED, sort=BuildListSort.SMALLEST_VOLUME, limit=4)
        assert [item.id for item in ordered] == [identifiers[2], identifiers[3], identifiers[0], identifiers[1]]


@pytest.mark.parametrize("sort", [BuildListSort.SMALLEST_VOLUME, BuildListSort.UPDATED, BuildListSort.NEWEST])
async def test_anchors_walk_a_sorted_listing_through_ties_and_unknown_values(
    migrated_session_factory: async_sessionmaker[AsyncSession], sort: BuildListSort
) -> None:
    identifiers = await seed(migrated_session_factory, 6)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await set_widths(migrated_session_factory, identifiers, (3, None, 2, 2, None, 1))
    display = list(await page_ids(repository, sort=sort, limit=6))

    for index, anchor in enumerate(display):
        after = await page_ids(repository, selector=PageSelector(after_id=anchor), sort=sort, limit=6)
        before = await page_ids(repository, selector=PageSelector(before_id=anchor), sort=sort, limit=6)
        assert list(after) == display[index + 1 :]
        assert list(before) == display[:index]


async def test_a_sorted_anchor_outside_the_listing_is_rejected(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 2)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    missing = max(identifiers) + 100

    with pytest.raises(PageAnchorNotFoundError):
        await page_ids(repository, selector=PageSelector(after_id=missing), sort=BuildListSort.SMALLEST_VOLUME)
    # The ID sort seeks on the anchor itself, so a deleted anchor still continues the walk.
    assert list(await page_ids(repository, selector=PageSelector(before_id=missing))) == []
    assert list(await page_ids(repository, selector=PageSelector(after_id=missing))) == sorted(identifiers)[::-1]
