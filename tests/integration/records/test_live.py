"""Live door lookup uses confirmed source facts without growing the record catalogue."""

from functools import partial

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.builds.models import DoorBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.live import DoorRecordLookup
from squid.records.application.services import RecordComputationService, RecordService
from squid.records.domain import BaseTitle, ResolutionStatus, TimingVariant
from squid.records.infrastructure import repository
from squid.records.infrastructure.repository import PostgresRecordRepository
from squid.records.models import (
    ComputationRun,
    DoorTimingVariant,
    RecordRecomputeQueueItem,
    RecordTitle,
    RulesRevision,
    TitleDefinition,
)
from tests.support.build_edits import build_edit_session
from tests.support.database import record_queries


def document() -> DoorExpansion:
    return DoorExpansion(
        parameters=(
            ExpansionParameter(
                name="n",
                minimum=1,
                maximum_kind="unbounded",
                initial_steps=(),
                repeating_steps=(1,),
            ),
        ),
        branches=(
            ExpansionBranch(
                opening_width="n",
                opening_height="3",
                opening_depth="1",
                volume="10ceil(n/2)",
                timings=(ExpansionTiming(method="opening", expression="floor(n/2)", unit="game_tick"),),
            ),
        ),
    )


async def test_live_lookup_does_not_materialize_large_sizes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        family = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            expansion=document(),
        )
        fixed = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=10,
            height=10,
            depth=1,
            normal_opening_time=10,
        )
        session.add_all([family, fixed])
        await session.flush()
        family_id, fixed_id = family.id, fixed.id
    models = (RecordTitle, TitleDefinition, ComputationRun, RulesRevision, RecordRecomputeQueueItem)
    async with sessions() as session:
        before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = RecordService(RecordComputationService(uow_factory=factory), uow_factory=factory)
    with record_queries(sessions) as statements:
        result = await service.lookup_door(DoorRecordLookup(opening=(6, 3, 1)))
        assert result.resolution.holder_ids == (family_id,)
        assert {c.build_id for c in result.configurations} == {family_id, fixed_id}
        fastest = await service.lookup_door(DoorRecordLookup(opening=(6, 3, 1), base_title=BaseTitle.FASTEST))
        assert fastest.resolution.holder_ids == (family_id,)
        large = await service.lookup_door(DoorRecordLookup(opening=(9999, 3, 1)))
        assert large.resolution.holder_ids == (family_id,)
        assert large.configurations[0].volume == 50000
        no_version = await service.lookup_door(DoorRecordLookup(opening=(6, 3, 1), version_id=32767))
        assert no_version.resolution.status is ResolutionStatus.NO_CANDIDATE
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
    async with sessions() as session:
        after = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    assert after == before


async def test_lookup_snapshot_does_not_mix_concurrent_measurement_edits(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        fixed = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=10,
            height=10,
            depth=1,
            normal_opening_time=10,
        )
        session.add(fixed)
        await session.flush()
        build_id = fixed.id
    original_timings = repository._door_timings

    async def edit_before_timing_read(
        session: AsyncSession,
        build_ids: tuple[int, ...],
    ) -> dict[int, tuple[TimingVariant, ...]]:
        assert await session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        async with build_edit_session(sessions) as editor:
            build = await editor.get(DoorBuild, build_id)
            assert build is not None
            build.width = 20
            editor.add(DoorTimingVariant(build_id=build_id, label="new", opening_time=1))
        return await original_timings(session, build_ids)

    monkeypatch.setattr(repository, "_door_timings", edit_before_timing_read)
    async with sessions() as session:
        sources = await PostgresRecordRepository(lambda: session).list_for_door_lookup((6, 3, 1), "Door", None)
    assert sources[0].candidate.fixed_volume == 100
    assert sources[0].candidate.timing_variants[0].values[0] == 10
    monkeypatch.setattr(repository, "_door_timings", original_timings)
    async with sessions() as session:
        sources = await PostgresRecordRepository(lambda: session).list_for_door_lookup((6, 3, 1), "Door", None)
    assert sources[0].candidate.fixed_volume == 200
    assert sources[0].candidate.timing_variants[0].values[0] == 1
