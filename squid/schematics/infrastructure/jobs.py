"""PostgreSQL adapter for durable schematic jobs."""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue, retry_delay
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobSnapshot,
)
from squid.schematics.infrastructure.models import SchematicJob


class PostgresSchematicJobRepository:
    """Claim, fence, and retain native-engine jobs in PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(
            session_factory,
            SchematicJob,
            ready_at=SchematicJob.available_at,
            claimed_at=SchematicJob.claimed_at,
            dead_at=SchematicJob.dead_at,
        )

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
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(SchematicJob)
                        .where(
                            SchematicJob.available_at <= func.now(),
                            SchematicJob.completed_at.is_(None),
                            self._queue.reclaimable(),
                        )
                        .order_by(SchematicJob.available_at, SchematicJob.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed_at = await self._queue.stamp(rows, session)
        return tuple(
            ClaimedSchematicJob(
                id=row.id,
                operation=cast(SchematicJobOperation, row.operation),
                params=cast(Mapping[str, Any], row.params),
                input_keys=tuple(row.input_keys),
                attempts=row.attempts,
                claimed_at=claimed_at,
            )
            for row in rows
        )

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
        *,
        retention_hours: int,
    ) -> bool:
        statement = (
            update(SchematicJob)
            .where(SchematicJob.id == job.id, SchematicJob.claimed_at == job.claimed_at)
            .values(
                result=dict(result),
                result_object_key=result_object_key,
                completed_at=func.now(),
                claimed_at=None,
                expires_at=func.now() + timedelta(hours=retention_hours),
                last_error=None,
                error_kind=None,
                error_context={},
            )
        )
        async with self._session_factory.begin() as session:
            outcome = cast(CursorResult[Any], await session.execute(statement))
        return bool(outcome.rowcount)

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
        attempts = job.attempts + 1
        values: dict[str, object] = {
            "attempts": attempts,
            "claimed_at": None,
            "last_error": error[:4000],
            "error_kind": error_kind,
            "error_context": dict(error_context),
        }
        dead = terminal or attempts >= max_attempts
        if dead:
            values.update(
                dead_at=func.now(),
                expires_at=func.now() + timedelta(hours=retention_hours),
            )
        else:
            values["available_at"] = func.now() + retry_delay(attempts)
        statement = (
            update(SchematicJob)
            .where(SchematicJob.id == job.id, SchematicJob.claimed_at == job.claimed_at)
            .values(**values)
        )
        async with self._session_factory.begin() as session:
            outcome = cast(CursorResult[Any], await session.execute(statement))
        return dead and bool(outcome.rowcount)

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
