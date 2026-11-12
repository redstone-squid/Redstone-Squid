"""PostgreSQL adapter for durable schematic jobs."""

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import RateLimitedError
from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.persistence.types import database_now
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicCleanupClaim,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobSnapshot,
)
from squid.schematics.models import SchematicJob

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
    # Keep completed rows for result polling, but exclude them from new work claims.
    pending=SchematicJob.completed_at.is_(None),
)


class PostgresSchematicJobRepository:
    """Persist native-engine job leases, outcomes, and retained outputs.

    The caller owns the transaction. Updates from workers apply only while their claim token
    matches; completed rows remain available for client polling until retention expires.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(SCHEMATIC_JOB_SPEC)

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        session = self._session()
        owner = params.get("video", {}).get("account_id") if operation == "render_video" else None
        if owner is not None:
            # Serialize account admission so simultaneous requests cannot exceed the queue budget.
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"video:{owner}", 0))))
            active = await session.scalar(
                select(func.count())
                .select_from(SchematicJob)
                .where(
                    SchematicJob.operation == "render_video",
                    SchematicJob.params["video"]["account_id"].astext == str(owner),
                    SchematicJob.completed_at.is_(None),
                    SchematicJob.dead_at.is_(None),
                )
            )
            if active is not None and active >= 2:
                raise RateLimitedError(retry_after=30)
        statement = (
            insert(SchematicJob)
            .values(operation=operation, params=dict(params), input_keys=list(input_keys), error_context={})
            .returning(SchematicJob.id)
        )
        session = self._session()
        job_id = await session.scalar(statement)
        assert job_id is not None
        return job_id

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        session = self._session()
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
            operation=row.operation,
            params=row.params,
            claimed_at=row.claimed_at,
            expires_at=row.expires_at,
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
            for row in await self._queue.claim(limit=limit, session=self._session())
        )

    async def renew(self, job: ClaimedSchematicJob) -> bool:
        statement = (
            update(SchematicJob)
            .where(
                SchematicJob.id == job.id,
                self._queue.held_by(job.claim_token),
                SchematicJob.completed_at.is_(None),
                SchematicJob.dead_at.is_(None),
            )
            .values(claimed_at=database_now())
            .returning(SchematicJob.id)
        )
        return await self._session().scalar(statement) is not None

    async def register_result_object(self, job: ClaimedSchematicJob, object_key: str) -> bool:
        statement = (
            update(SchematicJob)
            .where(SchematicJob.id == job.id, self._queue.held_by(job.claim_token))
            .values(
                result_object_keys=func.array_append(
                    func.array_remove(SchematicJob.result_object_keys, object_key), object_key
                )
            )
            .returning(SchematicJob.id)
        )
        session = self._session()
        return await session.scalar(statement) is not None

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
            session=self._session(),
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
            session=self._session(),
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
            terminal=terminal,
            values=diagnostics,
            dead_values={**diagnostics, "expires_at": func.now() + timedelta(hours=retention_hours)},
        )
        return outcome.dead_lettered

    async def claim_cleanup(self, *, limit: int) -> tuple[SchematicCleanupClaim, ...]:
        """Reserve expired jobs and collect their output keys for deletion outside the transaction.

        Input keys are shared content-addressed objects and are not owned by an individual job.
        """
        session = self._session()
        jobs = (
            await session.scalars(
                select(SchematicJob)
                .where(SchematicJob.expires_at <= func.now(), self._queue.reclaimable())
                .order_by(SchematicJob.expires_at, SchematicJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claims = []
        for job in jobs:
            token = uuid4()
            job.claim_token = token
            job.claimed_at = database_now()
            keys = tuple(
                dict.fromkeys(key for key in (*job.result_object_keys, job.result_object_key) if key is not None)
            )
            claims.append(SchematicCleanupClaim(job.id, token, keys))
        return tuple(claims)

    async def complete_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> int:
        session = self._session()
        removed = 0
        for job in jobs:
            removed += int(
                await session.scalar(
                    delete(SchematicJob)
                    .where(
                        SchematicJob.id == job.id,
                        SchematicJob.claim_token == job.claim_token,
                        SchematicJob.expires_at <= func.now(),
                    )
                    .returning(SchematicJob.id)
                )
                is not None
            )
        return removed

    async def release_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> None:
        session = self._session()
        for job in jobs:
            await session.execute(
                update(SchematicJob)
                .where(SchematicJob.id == job.id, SchematicJob.claim_token == job.claim_token)
                .values(claim_token=None, claimed_at=None)
            )
