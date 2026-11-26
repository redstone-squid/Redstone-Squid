"""Record result and holder triggers queue only search requests that change something."""

import pytest
from sqlalchemy import delete, select, update
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
        return {(key, action) for key, action in rows}


@pytest.fixture
async def held_results(migrated_session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """Two indexed results that share one holder."""
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
        result_ids: list[int] = []
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
            result_ids.append(result.id)
    await _refresh(migrated_session_factory)
    first, second = result_ids
    return first, second


async def test_a_holder_change_queues_its_result(
    migrated_session_factory: async_sessionmaker[AsyncSession], held_results: tuple[int, int]
) -> None:
    first, _ = held_results
    async with migrated_session_factory.begin() as session:
        await session.execute(update(RecordHolder).where(RecordHolder.result_id == first).values(rank=2))

    assert await _queued(migrated_session_factory) == {(f"result:{first}", "upsert")}


async def test_a_bulk_delete_queues_only_results_with_documents(
    migrated_session_factory: async_sessionmaker[AsyncSession], held_results: tuple[int, int]
) -> None:
    first, second = held_results
    async with migrated_session_factory.begin() as session:
        await session.execute(delete(SearchDocument).where(SearchDocument.source_key == f"result:{second}"))
        # Holders go with their results, and must not turn the delete back into an upsert.
        await session.execute(delete(RecordResult).where(RecordResult.id.in_(held_results)))

    assert await _queued(migrated_session_factory) == {(f"result:{first}", "delete")}
