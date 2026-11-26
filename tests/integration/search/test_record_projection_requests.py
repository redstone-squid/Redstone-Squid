"""Record changes queue each affected record once, by its public ID."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import OtherBuild
from squid.core.records.domain import BuildKind, RecordCriterion, VersionScope
from squid.core.records.models import (
    ComputationRun,
    MaterializationSource,
    Record,
    RecordDefinition,
    RecordHolder,
    RecordResult,
    RulesRevision,
)
from squid.core.search.infrastructure.projection import SearchProjectionWorker
from squid.core.search.models import SearchDocument, SearchProjectionQueueItem
from tests.support.build_edits import build_edit_session


async def _refresh(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions.begin() as session:
        while True:
            succeeded, failed = await SearchProjectionWorker(session).process_batch(
                context=ActingContext.worker("test-worker")
            )
            assert failed == 0
            if succeeded == 0:
                break


async def _queued(sessions: async_sessionmaker[AsyncSession]) -> set[tuple[str, str]]:
    async with sessions() as session:
        rows = await session.execute(
            select(SearchProjectionQueueItem.source_key, SearchProjectionQueueItem.action).where(
                SearchProjectionQueueItem.resource_kind == "record"
            )
        )
        return set(rows.tuples().all())


@dataclass(frozen=True)
class HeldRecords:
    """Two indexed records whose results share one holder."""

    records: tuple[UUID, UUID]
    results: tuple[int, int]
    build_id: int

    def keys(self) -> set[tuple[str, str]]:
        return {(f"record:{record_id}", "upsert") for record_id in self.records}


@pytest.fixture
async def held(migrated_session_factory: async_sessionmaker[AsyncSession]) -> HeldRecords:
    async with build_edit_session(migrated_session_factory) as session:
        account = Account()
        session.add(account)
        await session.flush()
        build = OtherBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            submission_time=Instant.now(),
            edited_time=Instant.now(),
        )
        rules = RulesRevision(document_hash="record-requests", calculator_version="1", formatter_version="1")
        session.add_all([build, rules])
        await session.flush()
        run = ComputationRun(rules_revision_id=rules.id, build_kind=BuildKind.DOOR, input_generation=1)
        session.add(run)
        await session.flush()
        records: list[UUID] = []
        results: list[int] = []
        for key in ("door:door|2x2|t[]|Door:r[]:p[]", "door:door|3x3|t[]|Door:r[]:p[]"):
            record = Record(
                criterion=RecordCriterion.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                category_key=key,
            )
            session.add(record)
            await session.flush()
            definition = RecordDefinition(
                record_id=record.public_id,
                rules_revision_id=rules.id,
                title="Smallest",
                materialization_source=MaterializationSource.EAGER,
            )
            session.add(definition)
            await session.flush()
            result = RecordResult(
                record_id=record.public_id, run_id=run.id, definition_id=definition.id, fingerprint=""
            )
            session.add(result)
            await session.flush()
            session.add(RecordHolder(result_id=result.id, build_id=build.id, rank=1, metric_snapshot={}, title="Door"))
            records.append(record.public_id)
            results.append(result.id)
    await _refresh(migrated_session_factory)
    return HeldRecords((records[0], records[1]), (results[0], results[1]), build.id)


async def _document_states(sessions: async_sessionmaker[AsyncSession]) -> dict[str, str | None]:
    async with sessions() as session:
        rows = await session.execute(
            select(SearchDocument.source_key, SearchDocument.status).where(SearchDocument.resource_kind == "record")
        )
        return dict(rows.tuples().all())


async def test_a_holder_change_queues_its_record(
    migrated_session_factory: async_sessionmaker[AsyncSession], held: HeldRecords
) -> None:
    async with migrated_session_factory.begin() as session:
        await session.execute(update(RecordHolder).where(RecordHolder.result_id == held.results[0]).values(rank=2))

    assert await _queued(migrated_session_factory) == {(f"record:{held.records[0]}", "upsert")}


async def test_a_bulk_delete_queues_each_record_once(
    migrated_session_factory: async_sessionmaker[AsyncSession], held: HeldRecords
) -> None:
    async with migrated_session_factory.begin() as session:
        # Holders go with their results; they must not queue anything of their own.
        await session.execute(delete(RecordResult).where(RecordResult.id.in_(held.results)))

    assert await _queued(migrated_session_factory) == held.keys()
    await _refresh(migrated_session_factory)
    # Neither record retired, so with no result neither is searchable.
    assert await _document_states(migrated_session_factory) == {}


async def test_a_retired_record_stays_searchable(
    migrated_session_factory: async_sessionmaker[AsyncSession], held: HeldRecords
) -> None:
    first, second = held.records
    async with migrated_session_factory.begin() as session:
        await session.execute(update(Record).where(Record.public_id == first).values(retired_at=Instant.now()))
        await session.execute(delete(RecordResult).where(RecordResult.id == held.results[0]))
    await _refresh(migrated_session_factory)

    assert await _document_states(migrated_session_factory) == {
        f"record:{first}": "retired",
        f"record:{second}": "current",
    }


async def test_recompute_bookkeeping_does_not_queue_records(
    migrated_session_factory: async_sessionmaker[AsyncSession], held: HeldRecords
) -> None:
    async with migrated_session_factory.begin() as session:
        await session.execute(update(Record).values(input_generation=func.coalesce(Record.input_generation, 0) + 1))

    assert await _queued(migrated_session_factory) == set()


async def test_records_follow_changes_to_their_holders_documents(
    migrated_session_factory: async_sessionmaker[AsyncSession], held: HeldRecords
) -> None:
    build_key = str(held.build_id)
    async with migrated_session_factory.begin() as session:
        await session.execute(select(func.upsert_search_projection_request("build", build_key, "upsert")))
    await _refresh(migrated_session_factory)
    async with migrated_session_factory() as session:
        refreshed = await session.scalar(
            select(SearchDocument.refreshed_at).where(
                SearchDocument.resource_kind == "record", SearchDocument.source_key == f"record:{held.records[0]}"
            )
        )
    async with migrated_session_factory.begin() as session:
        await session.execute(select(func.upsert_search_projection_request("build", build_key, "upsert")))
        await SearchProjectionWorker(session).process_batch(context=ActingContext.worker("test-worker"), limit=1)
    # The build did not change, so nothing that reads it was queued.
    assert await _queued(migrated_session_factory) == set()

    async with migrated_session_factory.begin() as session:
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.resource_kind == "build", SearchDocument.source_key == build_key)
            .values(title="Renamed Door")
        )
    assert await _queued(migrated_session_factory) == held.keys()
    await _refresh(migrated_session_factory)
    async with migrated_session_factory() as session:
        document = await session.scalar(
            select(SearchDocument).where(
                SearchDocument.resource_kind == "record", SearchDocument.source_key == f"record:{held.records[0]}"
            )
        )
    assert document is not None
    assert document.document_data["build_title"] == "Renamed Door"
    assert document.refreshed_at != refreshed
