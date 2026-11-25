"""Publication keeps one holder, the later ties, and builds that missing facts could promote."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, AnonymousActor
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import DoorBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.services import RecordComputationService
from squid.core.records.domain import BuildKind, CandidateGap, RecordCriterion, RecordFact
from tests.support.build_edits import build_edit_session


async def test_publication_separates_holder_tied_builds_and_contenders(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()

        def door(completed: int | None) -> DoorBuild:
            return DoorBuild(
                submitter_account_id=account.id,
                submission_status=BuildStatus.APPROVED,
                orientation="Door",
                door_width=2,
                door_height=2,
                width=4,
                height=4,
                depth=4,
                normal_opening_time=10,
                completion_at=None if completed is None else Instant(datetime(completed, 1, 1, tzinfo=UTC)),
            )

        earliest, later, undated = door(2020), door(2022), door(None)
        session.add_all([earliest, later, undated])
        await session.flush()
        earliest_id, later_id, undated_id = earliest.id, later.id, undated.id

    factory = partial(SqlAlchemyUnitOfWork, sessions)
    await RecordComputationService(uow_factory=factory).rebuild(
        kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor())
    )

    async with factory() as uow:
        listing = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=50
        )
        smallest = next(r for r in listing if r.criterion is RecordCriterion.SMALLEST)
        assert smallest
        assert smallest.holder_build_ids == (earliest_id,)
        assert smallest.tied_build_ids == (later_id,)
        assert smallest.gaps == (CandidateGap(undated_id, RecordFact.COMPLETION_AT),)
        gaps = await uow.records.list_gaps(kind=BuildKind.DOOR)
        assert smallest.definition_id in {gap.definition_id for gap in gaps}
