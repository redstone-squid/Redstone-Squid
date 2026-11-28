"""Publication ordering and catch-up use captured inputs, not worker completion order."""

from decimal import Decimal
from functools import partial
from typing import override
from unittest.mock import create_autospec
from uuid import uuid4

import anyio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, AnonymousActor
from squid.core.auth.application.viewer import ViewerQueries
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import DoorBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.models import ComputationBatch, RebuildSummary
from squid.core.records.application.preview import RecordPreviews
from squid.core.records.application.services import RecordComputationService
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import BuildKind
from squid.core.records.infrastructure.preview import PostgresPreviewReader
from squid.core.records.models import Record, RecordHolder, RecordInputGeneration, RecordRecomputeQueueItem
from squid.core.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.core.submissions.application.reviews import SubmissionDetail, SubmissionStatus, SubmissionSummary
from squid.core.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.core.tags.models import UnitDefinition
from squid.core.versions.application.services import VersionService
from tests.support.build_edits import build_edit_session


async def seed_door(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        door = DoorBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
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
        async def _compute(self, snapshot: RecordSnapshot) -> ComputationBatch:
            inputs.append(snapshot)
            captured.set()
            await release.wait()
            return await super()._compute(snapshot)

    old = BlockedComputation(uow_factory=factory)
    new = RecordComputationService(uow_factory=factory)

    async def rebuild_old() -> None:
        old_results.append(await old.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor())))

    with anyio.fail_after(20):
        async with anyio.create_task_group() as group:
            group.start_soon(rebuild_old)
            await captured.wait()
            assert inputs[0].sources[0].candidate.fixed_volume == 100
            async with build_edit_session(sessions) as session:
                door = await session.get(DoorBuild, build_id)
                assert door is not None
                door.width = 5
            newer = await new.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
            release.set()

    assert newer is not None
    assert newer.changed > 0
    assert old_results[0].changed == 0
    async with sessions() as session:
        generations = set(
            (await session.scalars(select(Record.input_generation).where(Record.retired_at.is_(None)))).all()
        )
        assert generations
        assert all(generation is not None and generation > inputs[0].input_generation for generation in generations)
        holders = (await session.scalars(select(RecordHolder))).all()
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
        async def _compute(self, snapshot: RecordSnapshot) -> ComputationBatch:
            if not self.edited:
                async with build_edit_session(sessions) as session:
                    door = await session.get(DoorBuild, build_id)
                    assert door is not None
                    door.width = 5
                self.edited = True
            return await super()._compute(snapshot)

    computation = EditingComputation(uow_factory=factory)
    await computation.process_queue(limit=1, context=ActingContext.worker("test-worker"))
    async with sessions() as session:
        pending = (
            await session.scalars(
                select(RecordRecomputeQueueItem).where(RecordRecomputeQueueItem.build_kind == BuildKind.DOOR)
            )
        ).one()
        assert pending.claim_token is None
        assert pending.build_id == build_id
        initial_generations = set((await session.scalars(select(Record.input_generation))).all())
        holders = (await session.scalars(select(RecordHolder))).all()
        assert holders
        assert all(holder.metric_snapshot.get("volume") == 100 for holder in holders)

    async with factory() as uow:
        records = (await uow.records.list_published_records(page_size=100)).items
        assert records
        assert all(record.refresh_pending for record in records)

    await computation.process_queue(context=ActingContext.worker("test-worker"))
    async with factory() as uow:
        records = (await uow.records.list_published_records(page_size=100)).items
        assert records
        assert all(not record.refresh_pending and record.last_checked_at is not None for record in records)
    async with sessions() as session:
        assert (await session.scalars(select(RecordRecomputeQueueItem))).all() == []
        generations = set((await session.scalars(select(Record.input_generation))).all())
        assert min(g for g in generations if g is not None) > max(g for g in initial_generations if g is not None)
        # Results update in place, so only the latest measurements remain.
        volumes = {holder.metric_snapshot.get("volume") for holder in await session.scalars(select(RecordHolder))}
        assert volumes == {50}


async def test_tag_changes_advance_both_input_generations(
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


async def test_predict_saved_draft_and_historical_edit_without_publishing(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    incumbent = await seed_door(sessions)
    computation = RecordComputationService(uow_factory=partial(SqlAlchemyUnitOfWork, sessions))
    ctx = ActingContext.direct(AnonymousActor())
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ctx)
    drafts = create_autospec(SubmissionDraftService, instance=True)
    views = create_autospec(ViewerQueries, instance=True)
    previews = RecordPreviews(
        PostgresPreviewReader(sessions, create_autospec(VersionService, instance=True)), computation, drafts, views
    )
    now = Instant.now()
    key = uuid4()
    drafts.get_accessible.return_value = StoredDraft(
        DraftSnapshot(
            id=key,
            owner_account_id=1,
            schema_id="build",
            schema_revision=1,
            category="door",
            revision=4,
            answers={
                "door_orientation": "door",
                "opening_width": 2,
                "opening_height": 2,
                "capture_width": 2,
                "capture_height": 3,
                "capture_depth": 1,
            },
        ),
        SubmissionOrigin.WEB,
        now,
        now,
        now,
    )
    result = await previews.draft(key, context=ctx)
    smallest = next(row for row in result.records if row.criterion == "smallest" and row.version_id is None)
    assert result.revision == 4
    assert smallest.outcome == "holds"
    assert smallest.record_id is not None
    assert [holder.build_id for holder in smallest.current_holders] == [incumbent]
    assert next(delta.difference for delta in smallest.current_holders[0].deltas if delta.metric == "volume") == -94
    first = next(row for row in result.records if row.criterion == "first")
    assert first.outcome == "undecided"
    assert first.missing[0].fields == ("completion",)
    drafts.get_accessible.assert_awaited_once_with(key, context=ctx)
    views.submission.return_value = SubmissionDetail(
        SubmissionSummary(
            key,
            incumbent,
            None,
            "edit",
            SubmissionStatus.PENDING,
            now.to_stdlib(),
            published=True,
            decided_revision=None,
        ),
        1,
        42,
        {"dimensions": [2, 3, 1]},
        1,
        {},
    )
    archived = await previews.submission(key, 42, context=ctx)
    views.submission.assert_awaited_once_with(key, context=ctx, review_session_id=42)
    edited = next(row for row in archived.records if row.criterion == "smallest")
    assert edited.outcome == "holds"
    assert edited.current_holders == smallest.current_holders
    async with sessions() as session:
        stored = await session.get(DoorBuild, incumbent)
        assert stored is not None
        assert stored.width == 10
