"""Publish computed records through the real schema and transaction boundary."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.domain import BuildCategory
from squid.builds.infrastructure.repository import BuildRepository
from squid.events.infrastructure.models import DomainEventRecord
from squid.records.application.models import (
    CategoryCompetition,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
)
from squid.records.domain import (
    BuildKind,
    CategoryText,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    ResolutionStatus,
    VersionScope,
)
from squid.records.infrastructure.models import RecordComputationRun, RecordHolderHistory
from squid.records.infrastructure.repository import PostgresRecordRepository
from tests.support.database import record_queries
from tests.support.submission_targets import seed_account_and_version, submission_build


def _batch(ruleset_id: int, build_ids: list[int]) -> ComputationBatch:
    started_at = datetime(2020, 1, 1, tzinfo=UTC)
    candidates = tuple(
        RecordCandidate(
            build_id=build_id,
            completion_at=started_at + timedelta(days=index),
            fixed_volume=20 - index,
        )
        for index, build_id in enumerate(build_ids)
    )
    competition = CategoryCompetition(
        identity=CategoryIdentity(BuildKind.DOOR, "door|2x3|t[20]|Door", ()),
        facets=(),
        category_text=CategoryText("2x3 Door"),
        candidates=candidates,
        candidate_version_ids=tuple((candidate.build_id, frozenset()) for candidate in candidates),
    )
    computed = ComputedRecord(
        record_class=RecordClass.SMALLEST,
        scope=VersionScope.ALL_TIME,
        version_id=None,
        competition=competition,
        title=CategoryText("Smallest 2x3 Door"),
        resolution=RecordResolution(ResolutionStatus.RESOLVED, holder_ids=(build_ids[-1],)),
        history=tuple(
            HolderHistoryEntry(
                build_ids=(candidate.build_id,),
                held_from=candidate.completion_at,
                held_until=candidates[index + 1].completion_at if index + 1 < len(candidates) else None,
            )
            for index, candidate in enumerate(candidates)
            if candidate.completion_at is not None
        ),
    )
    return ComputationBatch(ruleset_id=ruleset_id, kind=BuildKind.DOOR, version_id=None, records=(computed,))


async def test_activation_batches_history_and_replaces_the_previous_run(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    builds = BuildRepository(sessions)
    build_ids: list[int] = []
    for _ in range(5):
        build = submission_build(BuildCategory.DOOR, owner)
        await builds.save(build)
        assert build.id is not None
        build_ids.append(build.id)
    records = PostgresRecordRepository(sessions)
    batch = _batch(await records.active_ruleset_id(), build_ids)

    first_run = await records.activate(batch)
    with record_queries(sessions) as statements:
        current_run = await records.activate(batch)

    history_inserts = [
        statement for statement in statements if statement.startswith("INSERT INTO record_holder_history")
    ]
    assert len(history_inserts) == 1, "History must be inserted together, not flushed once per predecessor."
    async with sessions() as session:
        runs = (await session.execute(select(RecordComputationRun).order_by(RecordComputationRun.id))).scalars().all()
        history = (
            (
                await session.execute(
                    select(RecordHolderHistory)
                    .where(RecordHolderHistory.run_id == current_run)
                    .order_by(RecordHolderHistory.held_from)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await session.execute(
                    select(DomainEventRecord)
                    .where(DomainEventRecord.event_type == "record_run.activated")
                    .order_by(DomainEventRecord.id)
                )
            )
            .scalars()
            .all()
        )

    assert [(run.id, run.is_active) for run in runs] == [(first_run, False), (current_run, True)]
    assert [row.build_id for row in history] == build_ids
    assert [row.predecessor_id for row in history] == [None, *(row.id for row in history[:-1])]
    assert [row.held_until for row in history] == [*(row.held_from for row in history[1:]), None]
    assert [(event.aggregate_id, event.payload["previous_run_id"], event.payload["baseline"]) for event in events] == [
        (first_run, None, True),
        (current_run, first_run, False),
    ]
    published = await records.list_published_records(
        offset=0, after_id=None, before_id=None, descending=False, limit=10
    )
    assert len(published) == 1
    assert published[0].title == "Smallest 2x3 Door"
    assert published[0].holder_build_ids == (build_ids[-1],)
    assert isinstance(published[0].computed_at, datetime)
    assert published[0].computed_at.utcoffset() == timedelta(0)
