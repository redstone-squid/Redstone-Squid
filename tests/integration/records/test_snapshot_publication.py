"""Publication ordering and catch-up use captured inputs, not worker completion order."""

from decimal import Decimal
from functools import partial
from typing import override

import anyio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.models import DoorBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.models import ComputationBatch, RebuildSummary
from squid.records.application.services import RecordComputationService
from squid.records.application.snapshots import RecordSnapshot
from squid.records.domain import BuildKind
from squid.records.models import ComputationRun, RecordInputGeneration, RecordRecomputeQueueItem, TitleHolder
from squid.tags.models import UnitDefinition
from tests.support.build_edits import build_edit_session


async def seed_door(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        door = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            door_width=2,
            door_height=2,
            width=10,
            height=10,
            depth=1,
        )
        session.add(door)
        await session.flush()
        return door.id


async def test_older_snapshot_cannot_replace_a_newer_publication(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    build_id = await seed_door(sessions)
    captured = anyio.Event()
    release = anyio.Event()
    old_results: list[RebuildSummary] = []
    inputs: list[RecordSnapshot] = []
    newer: RebuildSummary | None = None

    class BlockedComputation(RecordComputationService):
        @override
        async def _compute_snapshot(self, snapshot: RecordSnapshot) -> ComputationBatch:
            inputs.append(snapshot)
            captured.set()
            await release.wait()
            return await super()._compute_snapshot(snapshot)

    old = BlockedComputation(uow_factory=factory)
    new = RecordComputationService(uow_factory=factory)

    async def rebuild_old() -> None:
        old_results.append(await old.rebuild(kinds=(BuildKind.DOOR,)))

    with anyio.fail_after(20):
        async with anyio.create_task_group() as group:
            group.start_soon(rebuild_old)
            await captured.wait()
            assert inputs[0].sources[0].candidate.fixed_volume == 100
            async with build_edit_session(sessions) as session:
                door = await session.get(DoorBuild, build_id)
                assert door is not None
                door.width = 5
            newer = await new.rebuild(kinds=(BuildKind.DOOR,))
            release.set()

    assert newer is not None
    assert old_results[0].run_ids == newer.run_ids
    async with sessions() as session:
        active = (await session.scalars(select(ComputationRun).where(ComputationRun.is_active))).one()
        assert active.checked_generation is not None
        assert active.checked_generation > inputs[0].input_generation
        holders = (await session.scalars(select(TitleHolder))).all()
        assert holders
        assert all(holder.metric_snapshot.get("volume") == 50 for holder in holders)


async def test_source_edit_during_computation_survives_atomic_acknowledgement(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    build_id = await seed_door(sessions)

    # Isolate the door claim from the extender work seeded by rule activation.
    async with factory() as uow:
        await uow.records.active_rules_revision_id()
        await uow.commit()
    async with sessions.begin() as session:
        await session.execute(
            delete(RecordRecomputeQueueItem).where(RecordRecomputeQueueItem.build_kind == BuildKind.EXTENDER)
        )

    class EditingComputation(RecordComputationService):
        edited = False

        @override
        async def _compute_snapshot(self, snapshot: RecordSnapshot) -> ComputationBatch:
            if not self.edited:
                async with build_edit_session(sessions) as session:
                    door = await session.get(DoorBuild, build_id)
                    assert door is not None
                    door.width = 5
                self.edited = True
            return await super()._compute_snapshot(snapshot)

    computation = EditingComputation(uow_factory=factory)
    await computation.process_queue(limit=1)
    async with sessions() as session:
        pending = (
            await session.scalars(
                select(RecordRecomputeQueueItem).where(RecordRecomputeQueueItem.build_kind == BuildKind.DOOR)
            )
        ).one()
        assert pending.claim_token is None
        active = (
            await session.scalars(
                select(ComputationRun).where(ComputationRun.is_active, ComputationRun.build_kind == BuildKind.DOOR)
            )
        ).one()
        initial_generation = active.checked_generation
        holders = (await session.scalars(select(TitleHolder))).all()
        assert holders
        assert all(holder.metric_snapshot.get("volume") == 100 for holder in holders)

    async with factory() as uow:
        records = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=100
        )
        assert records
        assert all(record.refresh_pending for record in records)

    await computation.process_queue()
    async with factory() as uow:
        records = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=100
        )
        assert records
        assert all(not record.refresh_pending and record.last_checked_at is not None for record in records)
    async with sessions() as session:
        assert (await session.scalars(select(RecordRecomputeQueueItem))).all() == []
        active = (
            await session.scalars(
                select(ComputationRun).where(ComputationRun.is_active, ComputationRun.build_kind == BuildKind.DOOR)
            )
        ).one()
        assert active.checked_generation is not None
        assert initial_generation is not None
        assert active.checked_generation > initial_generation
        # Previous publication evidence remains available after catch-up.
        volumes = {holder.metric_snapshot.get("volume") for holder in await session.scalars(select(TitleHolder))}
        assert volumes == {50, 100}


async def test_taxonomy_changes_advance_both_input_generations(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with sessions() as session:
        before = dict(
            (await session.execute(select(RecordInputGeneration.build_kind, RecordInputGeneration.generation))).all()
        )
    async with sessions.begin() as session:
        session.add(UnitDefinition(key="test_record_unit", dimension="length", symbol="u", scale_to_base=Decimal(2)))
    async with sessions() as session:
        after = dict(
            (await session.execute(select(RecordInputGeneration.build_kind, RecordInputGeneration.generation))).all()
        )
        assert after == {kind: generation + 1 for kind, generation in before.items()}
        kinds = set((await session.scalars(select(RecordRecomputeQueueItem.build_kind))).all())
        assert kinds == {BuildKind.DOOR, BuildKind.EXTENDER}
