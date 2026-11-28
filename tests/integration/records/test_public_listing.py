"""Public record lists filter, sort and page current results, with holder facts attached."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, AnonymousActor
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import DoorBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.metrics import MetricUnit, RecordMetric
from squid.core.records.application.models import RecordListFilters, RecordListSort, RecordStatus
from squid.core.records.application.services import RecordComputationService
from squid.core.records.domain import BuildKind, RecordCriterion, VersionScope
from tests.support.build_edits import build_edit_session


async def test_listing_filters_sorts_and_reports_holder_facts(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()

        def door(size: int, year: int) -> DoorBuild:
            return DoorBuild(
                submitter_account_id=account.id,
                submission_status=BuildStatus.APPROVED,
                orientation="Door",
                door_width=size,
                door_height=size,
                width=4,
                height=4,
                depth=size,
                normal_opening_time=10,
                completion_at=Instant(datetime(year, 1, 1, tzinfo=UTC)),
            )

        small, large = door(2, 2020), door(3, 2021)
        session.add_all([small, large])
        await session.flush()
        small_id = small.id

    factory = partial(SqlAlchemyUnitOfWork, sessions)
    await RecordComputationService(uow_factory=factory).rebuild(
        kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor())
    )

    async with factory() as uow:
        records = uow.records
        smallest = RecordListFilters(criteria=(RecordCriterion.SMALLEST,), version_scopes=(VersionScope.ALL_TIME,))
        assert await records.count_published_records(filters=smallest) == 2
        by_title = await records.list_published_records(sort=RecordListSort.TITLE, filters=smallest, page_size=1)
        (first,) = by_title.items
        assert by_title.next is not None
        (second,) = (
            await records.list_published_records(
                selector=by_title.next.selector(), sort=RecordListSort.TITLE, filters=smallest, page_size=5
            )
        ).items
        assert first.one_line_title.lower() < second.one_line_title.lower()
        two = next(record for record in (first, second) if record.holder_build_ids == (small_id,))
        assert two.category_key.startswith("door:door|2x2|")
        assert two.holder_since == datetime(2020, 1, 1, tzinfo=UTC)
        assert two.metrics["volume"] == RecordMetric(32, MetricUnit.BLOCKS, "32 blocks")
        assert two.metrics["opening_time"].value == 10
        assert two.status is RecordStatus.RESOLVED

        everything = await records.count_published_records(filters=RecordListFilters())
        assert await records.count_published_records(filters=RecordListFilters(statuses=(RecordStatus.RESOLVED,))) == (
            everything
        )
        assert await records.count_published_records(filters=RecordListFilters(build_kinds=(BuildKind.EXTENDER,))) == 0
        assert await records.count_published_records(filters=RecordListFilters(tag_slugs=("no-such-tag",))) == 0
        assert await records.count_published_records(filters=RecordListFilters(tag_ids=(2**40,))) == 0
        incompatible = RecordListFilters(statuses=(RecordStatus.RESOLVED, RecordStatus.UNRESOLVED))
        page = await records.list_published_records(filters=incompatible, page_size=1)
        assert (page.total, page.items) == (0, ())
        recent = (
            await records.list_published_records(sort=RecordListSort.RECENTLY_CHANGED, page_size=everything)
        ).items
        assert [record.computed_at for record in recent] == sorted(
            (record.computed_at for record in recent), reverse=True
        )

        (period,) = await records.get_record_history(two.record_id)
        assert period.build_ids == (small_id,)
        assert period.held_until is None
        assert period.metrics == two.metrics
        siblings = await records.list_siblings(two.record_id)
        assert two.record_id not in {sibling.record_id for sibling in siblings}
        assert {(s.criterion, s.version_scope) for s in siblings} >= {
            (RecordCriterion.FASTEST, VersionScope.ALL_TIME),
            (RecordCriterion.FIRST, VersionScope.ALL_TIME),
        }
        assert all("2x2" in sibling.title for sibling in siblings)
