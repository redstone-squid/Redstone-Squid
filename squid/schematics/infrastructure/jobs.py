"""PostgreSQL adapter for durable schematic jobs."""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobSnapshot,
)
from squid.schematics.infrastructure.models import SchematicJob

SCHEMATIC_JOB_SPEC = QueueSpec(
    name="schematic_jobs",
    model=SchematicJob,
    key=(SchematicJob.id,),
    available_at=SchematicJob.available_at,
    claimed_at=SchematicJob.claimed_at,
    claim_token=SchematicJob.claim_token,
    attempts=SchematicJob.attempts,
    last_error=SchematicJob.last_error,
    dead_at=SchematicJob.dead_at,
    # The only queue that retains acknowledged rows, so readiness has to exclude
    # the completed ones every other queue deletes.
    pending=SchematicJob.completed_at.is_(None),
)


class PostgresSchematicJobRepository:
    """Claim, fence, and retain native-engine jobs in PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(SCHEMATIC_JOB_SPEC, session_factory)

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        statement = (
            insert(SchematicJob)
            .values(operation=operation, params=dict(params), input_keys=list(input_keys), error_context={})
            .returning(SchematicJob.id)
        )
        async with self._session_factory.begin() as session:
            job_id = await session.scalar(statement)
        assert job_id is not None
        return job_id

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(SchematicJob, job_id)
        if row is None:
            return None
        return SchematicJobSnapshot(
            id=row.id,
            completed_at=row.completed_at,
            dead_at=row.dead_at,
            result=cast(Mapping[str, Any] | None, row.result),
            result_object_key=row.result_object_key,
            last_error=row.last_error,
            error_kind=cast(SchematicJobErrorKind | None, row.error_kind),
            error_context=cast(Mapping[str, Any], row.error_context),
        )

    async def claim(self, *, limit: int) -> Sequence[ClaimedSchematicJob]:
        return tuple(
            ClaimedSchematicJob(
                id=row.id,
                operation=cast(SchematicJobOperation, row.operation),
                params=cast(Mapping[str, Any], row.params),
                input_keys=tuple(row.input_keys),
                attempts=row.attempts,
                claim_token=self._queue.token_of(row),
            )
            for row in await self._queue.claim(limit=limit)
        )

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
        *,
        retention_hours: int,
    ) -> bool:
        # This queue answers client polls from the row, so acknowledging it means
        # writing the terminal state rather than deleting the work.
        outcome = await self._queue.complete(
            (SchematicJob.id == job.id,),
            job.claim_token,
            values={
                "result": dict(result),
                "result_object_key": result_object_key,
                "completed_at": func.now(),
                "expires_at": func.now() + timedelta(hours=retention_hours),
                "last_error": None,
                "error_kind": None,
                "error_context": {},
            },
        )
        return outcome.applied

    async def fail(
        self,
        job: ClaimedSchematicJob,
        error: str,
        *,
        error_kind: SchematicJobErrorKind,
        error_context: Mapping[str, Any],
        max_attempts: int,
        terminal: bool,
        retention_hours: int,
    ) -> bool:
        diagnostics: dict[str, Any] = {"error_kind": error_kind, "error_context": dict(error_context)}
        outcome = await self._queue.fail(
            (SchematicJob.id == job.id,),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
            terminal=terminal,
            values=diagnostics,
            dead_values={**diagnostics, "expires_at": func.now() + timedelta(hours=retention_hours)},
        )
        return outcome.dead_lettered

    async def cleanup(self, *, limit: int) -> Sequence[str]:
        candidates = (
            select(SchematicJob.id)
            .where(SchematicJob.expires_at <= func.now())
            .order_by(SchematicJob.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        statement = (
            delete(SchematicJob).where(SchematicJob.id.in_(candidates)).returning(SchematicJob.result_object_key)
        )
        async with self._session_factory.begin() as session:
            rows = (await session.execute(statement)).scalars().all()
        return tuple(key for key in rows if key is not None)
