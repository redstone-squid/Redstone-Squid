"""Live door lookup uses confirmed source facts without growing the record catalogue."""

from functools import partial

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.builds.domain import BuildStatus
from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.core.builds.models import BuildVersion, DoorBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.expansion_regions import DoorRangeQuery
from squid.core.records.application.live import DoorRecordLookup
from squid.core.records.application.services import RecordComputationService, RecordService
from squid.core.records.domain import BuildKind, RecordCriterion, ResolutionStatus, TimingVariant
from squid.core.records.domain.regions import RegionAnalysisStatus
from squid.core.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from squid.core.records.infrastructure.repository import PostgresRecordRepository
from squid.core.records.models import (
    ComputationRun,
    DoorTimingVariant,
    Record,
    RecordDefinition,
    RecordRecomputeQueueItem,
    RulesRevision,
)
from squid.core.versions.models import Version
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
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            expansion=document(),
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
        )
        session.add_all([family, fixed])
        await session.flush()
        family_id, fixed_id = family.id, fixed.id
    models = (Record, RecordDefinition, ComputationRun, RulesRevision, RecordRecomputeQueueItem)
    async with sessions() as session:
        before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = RecordService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, range_analyzer=SubprocessRangeAnalyzer()
    )
    with record_queries(sessions) as statements:
        result = await service.lookup_door(DoorRecordLookup(opening=(6, 3, 1)))
        assert result.resolution.holder_ids == (family_id,)
        assert {c.build_id for c in result.configurations} == {family_id, fixed_id}
        fastest = await service.lookup_door(DoorRecordLookup(opening=(6, 3, 1), criterion=RecordCriterion.FASTEST))
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


@pytest.mark.parametrize("publication", [False, True])
async def test_lookup_snapshot_does_not_mix_concurrent_measurement_edits(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    publication: bool,
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
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
        )
        session.add(fixed)
        await session.flush()
        build_id = fixed.id
    original_timings = PostgresRecordRepository._door_timings

    async def edit_before_timing_read(
        self: PostgresRecordRepository,
        build_ids: tuple[int, ...],
    ) -> dict[int, tuple[TimingVariant, ...]]:
        session = self._session()
        assert await session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await session.scalar(text("SHOW transaction_read_only")) == ("off" if publication else "on")
        async with build_edit_session(sessions) as editor:
            build = await editor.get(DoorBuild, build_id)
            assert build is not None
            build.width = 20
            editor.add(DoorTimingVariant(build_id=build_id, label="new", opening_time=1))
        return await original_timings(self, build_ids)

    monkeypatch.setattr(PostgresRecordRepository, "_door_timings", edit_before_timing_read)
    async with sessions() as session:
        records = PostgresRecordRepository(lambda: session)
        if publication:
            await records.begin_computation()
            sources = await records.list_confirmed(BuildKind.DOOR)
        else:
            sources = await records.list_for_door_lookup((6, 3, 1), "Door", None)
    assert sources[0].candidate.fixed_volume == 100
    assert sources[0].candidate.timing_variants[0].values[0] == 10
    monkeypatch.setattr(PostgresRecordRepository, "_door_timings", original_timings)
    async with sessions() as session:
        sources = await PostgresRecordRepository(lambda: session).list_for_door_lookup((6, 3, 1), "Door", None)
    assert sources[0].candidate.fixed_volume == 200
    assert sources[0].candidate.timing_variants[0].values[0] == 1


async def test_width_lookup_reads_stored_families_and_fixed_exceptions_without_writes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        version = Version(edition="Java", major_version=1, minor_version=21, patch_number=0)
        session.add_all([account, version])
        await session.flush()
        family = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            expansion=document(),
            build_versions=[BuildVersion(version_id=version.id)],
        )
        fixed = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=1,
            height=1,
            depth=1,
            build_versions=[BuildVersion(version_id=version.id)],
        )
        other_version = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            orientation="Door",
            door_width=9,
            door_height=3,
            width=1,
            height=1,
            depth=1,
        )
        pending = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.PENDING,
            orientation="Door",
            expansion=DoorExpansion(raw_description="not reviewed"),
            build_versions=[BuildVersion(version_id=version.id)],
        )
        session.add_all([family, fixed, other_version, pending])
        await session.flush()
        family_id, fixed_id, version_id = family.id, fixed.id, version.id
    models = (Record, RecordDefinition, ComputationRun, RulesRevision, RecordRecomputeQueueItem)
    async with sessions() as session:
        before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = RecordService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, range_analyzer=SubprocessRangeAnalyzer()
    )
    with record_queries(sessions) as statements:
        result = await service.lookup_door_ranges(DoorRangeQuery(height=3, version_id=version_id))
        assert result.analysis.status is RegionAnalysisStatus.EXACT
        assert {c.build_id for c in result.configurations} == {family_id, fixed_id}
        for width in (1, 2, 5, 6, 7, 9, 9999):
            region = next(r for r in result.analysis.regions if r.domain.contains(width))
            expected = await service.lookup_door(DoorRecordLookup(opening=(width, 3, 1), version_id=version_id))
            assert region.resolution == expected.resolution
        clipped = await service.lookup_door_ranges(
            DoorRangeQuery(
                height=3,
                minimum=7,
                maximum=9999,
                version_id=version_id,
            )
        )
        assert {c.build_id for c in clipped.configurations} == {family_id}
        assert all(r.resolution.holder_ids == (family_id,) for r in clipped.analysis.regions)
        empty = await service.lookup_door_ranges(DoorRangeQuery(height=3, orientation="Skydoor"))
        assert empty.analysis.status is RegionAnalysisStatus.EXACT
        assert empty.analysis.regions[0].resolution.status is ResolutionStatus.NO_CANDIDATE
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
    async with sessions() as session:
        after = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    assert after == before
