"""Families compete through ordinary publication and retain the evaluated source facts."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import DoorBuild
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.models import RecordLookupRequest
from squid.core.records.application.services import RecordComputationService, RecordService
from squid.core.records.domain import BuildKind, RecordCriterion
from squid.core.records.models import RecordHolder, RecordResult
from squid.core.search.infrastructure.projection import SearchProjectionLoader, SearchProjectionRepository
from tests.integration.records.test_live import document
from tests.support.build_edits import build_edit_session


async def test_ordinary_publication_uses_saved_configuration_and_survives_source_edits(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        family = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            expansion=document(),
            door_width=99,
            door_height=3,
            width=1,
            height=1,
            depth=1,
            normal_opening_time=0,
            completion_at=Instant(datetime(2000, 1, 1, tzinfo=UTC)),
        )
        fixed = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=10,
            height=10,
            depth=1,
            normal_opening_time=10,
            completion_at=Instant(datetime(2020, 1, 1, tzinfo=UTC)),
        )
        session.add_all([family, fixed])
        await session.flush()
        family_id, fixed_id = family.id, fixed.id
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    computation = RecordComputationService(uow_factory=factory)
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(Actor()))
    async with factory() as uow:
        listing = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=20
        )
        assert len(listing) == 5
        assert all(r.door_evidence is None for r in listing)
        assert all("6x3" in r.title for r in listing)
        smallest = next(r for r in listing if r.criterion is RecordCriterion.SMALLEST)
        first = next(r for r in listing if r.criterion is RecordCriterion.FIRST)
        assert smallest.holder_build_ids == (family_id,)
        assert first.holder_build_ids == (fixed_id,)
        detail = await uow.records.get_published_record(smallest.result_id)
        assert detail is not None
        assert detail.door_evidence is not None
        original_evidence = detail.door_evidence
        configuration = next(c for c in original_evidence.evaluation.configurations if c.build_id == family_id)
        assert configuration.parameters == (("n", 6),)
        assert configuration.volume == 30
        assert configuration.timing_variants == ((3, None, None, None, None, None),)
    async with sessions() as session:
        holder = await session.scalar(select(RecordHolder).where(RecordHolder.result_id == smallest.result_id))
        assert holder is not None
        assert holder.completion_at is None
        assert holder.metric_snapshot == {
            "opening": [6, 3, 1],
            "configuration_ids": [configuration.id],
            "volume": 30,
            "timing": [3, None, None, None, None, None],
        }
        result = await session.get(RecordResult, smallest.result_id)
        assert result is not None
        assert not result.history_complete
        loader = SearchProjectionLoader(session)
        build_projection = await loader.load("build", str(family_id))
        assert build_projection is not None
        await SearchProjectionRepository(session).replace(build_projection)
        projection = await loader.load("record", f"result:{smallest.result_id}")
        assert projection is not None
        assert projection.document_data["holder_metrics"] == {str(family_id): holder.metric_snapshot}

    async with build_edit_session(sessions) as session:
        family = await session.get(DoorBuild, family_id)
        assert family is not None
        family.expansion = document().model_copy(
            update={
                "branches": (document().branches[0].model_copy(update={"volume": "100n"}),),
            }
        )
    async with factory() as uow:
        unchanged = await uow.records.get_published_record(smallest.result_id)
        assert unchanged is not None
        assert unchanged.door_evidence == original_evidence
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(Actor()))
    async with factory() as uow:
        listing = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=20
        )
        replacement = next(r for r in listing if r.criterion is RecordCriterion.SMALLEST)
        assert replacement.record_id == smallest.record_id
        assert replacement.holder_build_ids == (fixed_id,)
        assert await uow.records.get_published_record(smallest.result_id) is None
    # Explicitly requesting a large size creates only that category, using the new source facts.
    service = RecordService(computation, permissions=PermissionService(uow_factory=factory), uow_factory=factory)
    await service.lookup_or_materialize(
        RecordLookupRequest(kind=BuildKind.DOOR, base_key="door|9999x3|t[]|Door", restriction_ids=frozenset()),
        context=ActingContext.direct(Actor(account_id=1, is_bot_owner=True)),
    )
    async with factory() as uow:
        listing = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=20
        )
        assert len(listing) == 10
        large = next(r for r in listing if r.criterion is RecordCriterion.SMALLEST and "9999x3" in r.title)
        assert large.holder_build_ids == (family_id,)
        detail = await uow.records.get_published_record(large.result_id)
        assert detail is not None
        assert detail.door_evidence is not None
        assert detail.door_evidence.evaluation.configurations[0].volume == 999900
