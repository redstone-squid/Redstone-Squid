"""Build paging by cursor preserves display order and agrees with count and offset queries."""

from collections.abc import Sequence
from dataclasses import replace
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.builds.application.queries import BuildListSort
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, OtherBuild
from squid.core.pagination import FIRST_PAGE, InvalidPageCursorError, Page, PageAnchor, PageSelector
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


async def page(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    *,
    selector: PageSelector = FIRST_PAGE,
    page_size: int = 2,
    sort: BuildListSort = BuildListSort.ID,
) -> Page[Build]:
    return await transact(
        repository,
        lambda uow: uow.builds.list_page(statuses=_CONFIRMED, sort=sort, selector=selector, page_size=page_size),
    )


def ids(page: Page[Build]) -> list[int]:
    # ORM identity columns may still be None before flush.
    return [build.id for build in page.items if build.id is not None]  # pyright: ignore[reportUnnecessaryComparison]


def follow(anchor: PageAnchor | None) -> PageSelector:
    assert anchor is not None
    return anchor.selector()


async def test_going_back_returns_the_previous_page_in_display_order(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    newest_first = list(reversed(identifiers))

    first = await page(repository)
    second = await page(repository, selector=follow(first.next))
    back = await page(repository, selector=follow(second.prev))

    assert ids(second) == newest_first[2:4]
    assert ids(back) == ids(first) == newest_first[:2]
    assert back.prev is None


async def test_an_offset_page_and_a_cursor_page_agree(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    by_offset = await page(repository, selector=PageSelector(offset=2))
    by_cursor = await page(repository, selector=follow((await page(repository)).next))

    assert ids(by_offset) == ids(by_cursor)
    assert by_offset.next == PageAnchor(offset=4)


async def test_the_total_matches_what_paging_actually_returns(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(migrated_session_factory, 5)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    current = await page(repository)
    walked = ids(current)
    while current.next is not None:
        current = await page(repository, selector=follow(current.next))
        walked.extend(ids(current))

    assert current.total == 5
    assert len(set(walked)) == len(walked) == 5


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
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await set_widths(migrated_session_factory, identifiers, (3, None, 2, 2))

    ordered = await page(repository, sort=BuildListSort.SMALLEST_VOLUME, page_size=4)

    assert ids(ordered) == [identifiers[2], identifiers[3], identifiers[0], identifiers[1]]


@pytest.mark.parametrize(
    "sort", [BuildListSort.SMALLEST_VOLUME, BuildListSort.UPDATED, BuildListSort.NEWEST, BuildListSort.TITLE]
)
async def test_cursors_walk_a_sorted_listing_through_ties_and_unknown_values(
    migrated_session_factory: async_sessionmaker[AsyncSession], sort: BuildListSort
) -> None:
    identifiers = await seed(migrated_session_factory, 6)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await set_widths(migrated_session_factory, identifiers, (3, None, 2, 2, None, 1))
    display = ids(await page(repository, sort=sort, page_size=6))

    forward = [await page(repository, sort=sort, page_size=1)]
    while forward[-1].next is not None:
        forward.append(await page(repository, selector=follow(forward[-1].next), sort=sort, page_size=1))
    backward = [forward[-1]]
    while backward[-1].prev is not None:
        backward.append(await page(repository, selector=follow(backward[-1].prev), sort=sort, page_size=1))

    assert [build_id for item in forward for build_id in ids(item)] == display
    assert [build_id for item in reversed(backward) for build_id in ids(item)] == display


async def test_a_cursor_survives_its_boundary_row_leaving_the_listing(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifiers = await seed(migrated_session_factory, 4)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    first = await page(repository, sort=BuildListSort.NEWEST)
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as uow, uow.builds.editing(actor="test", source="test"):
        boundary = await uow.builds.get_for_update(ids(first)[-1])
        assert boundary is not None
        boundary.submission_status = BuildStatus.PENDING
        await uow.commit()

    second = await page(repository, selector=follow(first.next), sort=BuildListSort.NEWEST)

    assert ids(second) == list(reversed(identifiers))[2:]


async def test_a_cursor_from_another_sort_is_rejected(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(migrated_session_factory, 3)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    newest = await page(repository, sort=BuildListSort.NEWEST)
    assert newest.next is not None
    assert newest.next.cursor is not None
    mistyped = replace(newest.next.cursor, key="yesterday")

    with pytest.raises(InvalidPageCursorError):
        await page(repository, selector=follow(newest.next), sort=BuildListSort.OLDEST)
    with pytest.raises(InvalidPageCursorError):
        await page(repository, selector=PageSelector(cursor=mistyped), sort=BuildListSort.NEWEST)
