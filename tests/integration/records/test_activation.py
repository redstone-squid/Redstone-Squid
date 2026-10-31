"""Publish computed records through the real schema and transaction boundary."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.domain import BuildCategory
from squid.builds.infrastructure.repository import BuildRepository
from squid.events.infrastructure.models import DomainEventRecord
from squid.records.application.models import (
    CandidateFacet,
    CategoryCompetition,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
)
from squid.records.domain import (
    BuildKind,
    FormattedTitle,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    ResolutionStatus,
    VersionScope,
)
from squid.records.infrastructure.models import (
    RecordComputationRun,
    RecordDefinitionFacet,
    RecordHolderHistory,
    RecordMaterializationSource,
    RecordRule,
    RecordRuleset,
)
from squid.records.infrastructure.repository import PostgresRecordRepository
from squid.versions.infrastructure.models import Version
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
        category_text=FormattedTitle("2x3 Door"),
        candidates=candidates,
        candidate_version_ids=tuple((candidate.build_id, frozenset()) for candidate in candidates),
    )
    computed = ComputedRecord(
        record_class=RecordClass.SMALLEST,
        scope=VersionScope.ALL_TIME,
        version_id=None,
        competition=competition,
        title=FormattedTitle("Smallest 2x3 Door"),
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


async def test_bulk_definitions_keep_public_requests_and_stable_competition_ids(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    build = submission_build(BuildCategory.DOOR, owner)
    await BuildRepository(sessions).save(build)
    assert build.id is not None
    records = PostgresRecordRepository(sessions)
    seed = _batch(await records.active_ruleset_id(), [build.id])
    template = seed.records[0]
    # Cross the SQLAlchemy bulk page boundary; every category expands into five record classes.
    batch = replace(
        seed,
        records=tuple(
            replace(
                template,
                record_class=record_class,
                title=FormattedTitle(
                    record_class.value, subtitle="First completion" if record_class is RecordClass.FIRST else None
                ),
                competition=replace(
                    template.competition,
                    identity=CategoryIdentity(BuildKind.DOOR, f"door|{size}x3|t[20]|Door", (1,)),
                    facets=(CandidateFacet(id=1, kind="restriction", name="Flush"),),
                ),
                history=(),
            )
            for size in range(1, 221)
            for record_class in RecordClass
        ),
    )
    with record_queries(sessions) as first_queries:
        await records.activate(batch)
    identity = batch.records[0].competition.identity
    titles = {record_class: FormattedTitle(f"Requested {record_class.value}") for record_class in RecordClass}
    await records.save_requested_category(batch.ruleset_id, identity, titles)
    assert identity in await records.list_requested_categories(BuildKind.DOOR)

    async with sessions() as session:
        original = {
            (row.record_class, row.category_key): (row.id, row.competition_id)
            for row in (await session.scalars(select(RecordRule))).all()
        }
    with record_queries(sessions) as repeated_queries:
        await records.activate(batch)
    assert identity in await records.list_requested_categories(BuildKind.DOOR)

    requested = CategoryIdentity(BuildKind.DOOR, "door|999x3|t[20]|Door", (1, 2))

    async def request_category() -> None:
        await records.save_requested_category(batch.ruleset_id, requested, titles)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(request_category)
            tasks.start_soon(request_category)

    async with sessions() as session:
        definitions = (await session.scalars(select(RecordRule))).all()
        facets = (await session.scalars(select(RecordDefinitionFacet))).all()
    assert len(definitions) == 1105
    assert len(facets) == 1110
    for definition in definitions:
        key = (definition.record_class, definition.category_key)
        if key in original:
            assert (definition.id, definition.competition_id) == original[key]
        if definition.category_key in {identity.key, requested.key}:
            assert definition.materialization_source is RecordMaterializationSource.PUBLIC_LOOKUP
    for queries in (first_queries, repeated_queries):
        competition_writes = [query for query in queries if query.startswith("INSERT INTO record_competitions")]
        definition_writes = [query for query in queries if query.startswith("INSERT INTO record_definitions")]
        assert len(competition_writes) <= 2
        assert len(definition_writes) <= 2

    async with sessions.begin() as session:
        next_ruleset = RecordRuleset(
            document_hash="next-test-ruleset", calculator_version="next", formatter_version="same"
        )
        session.add(next_ruleset)
        await session.flush()
        next_ruleset_id = next_ruleset.id
        version_id = await session.scalar(select(Version.id).where(Version.data_version == 3953))
    assert version_id is not None
    next_batch = replace(batch, ruleset_id=next_ruleset_id, records=batch.records[:5])
    await records.activate(next_batch)
    await records.activate(
        replace(
            next_batch,
            version_id=version_id,
            records=tuple(
                replace(record, scope=VersionScope.CURRENT, version_id=version_id) for record in next_batch.records
            ),
        )
    )
    async with sessions() as session:
        next_definitions = (
            await session.scalars(select(RecordRule).where(RecordRule.ruleset_id == next_ruleset_id))
        ).all()
    assert len(next_definitions) == 10
    for definition in next_definitions:
        previous_id, previous_public_id = original[(definition.record_class, definition.category_key)]
        assert definition.id != previous_id
        if definition.version_scope is VersionScope.ALL_TIME:
            assert definition.competition_id == previous_public_id
        else:
            assert definition.competition_id != previous_public_id
