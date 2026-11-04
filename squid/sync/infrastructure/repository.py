"""PostgreSQL Discord reconciliation queue adapter."""

import uuid
from collections.abc import Callable

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import DataIntegrityError
from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.sync.application import ReconciliationAction, ReconciliationJob, ReconciliationResource
from squid.sync.models import DiscordSyncQueueItem

DISCORD_SYNC_QUEUE_SPEC = QueueSpec(
    name="discord_sync",
    model=DiscordSyncQueueItem,
    key=(DiscordSyncQueueItem.id,),
    available_at=DiscordSyncQueueItem.available_at,
    enqueued_at=DiscordSyncQueueItem.enqueued_at,
    claimed_at=DiscordSyncQueueItem.claimed_at,
    claim_token=DiscordSyncQueueItem.claim_token,
    attempts=DiscordSyncQueueItem.attempts,
    last_error=DiscordSyncQueueItem.last_error,
    dead_at=DiscordSyncQueueItem.dead_at,
)


def _job(row: DiscordSyncQueueItem, token: uuid.UUID) -> ReconciliationJob:
    """Convert a claimed row to a job, validating its resource and action names.

    Invalid names violate database constraints and raise DataIntegrityError.
    """
    try:
        resource_kind = ReconciliationResource(row.resource_kind)
        action = ReconciliationAction(row.action)
    except ValueError as error:
        msg = "A reconciliation row holds a value its check constraint should have rejected."
        raise DataIntegrityError(
            msg,
            context={"id": row.id, "resource_kind": row.resource_kind, "action": row.action},
        ) from error
    return ReconciliationJob(
        id=row.id,
        resource_kind=resource_kind,
        source_key=row.source_key,
        action=action,
        generation=row.generation,
        attempts=row.attempts,
        claim_token=token,
    )


class PostgresDiscordSyncQueue:
    """Lease reconciliation jobs in the caller's transaction.

    Completion and failure updates only apply while the job's claim token still
    matches. The caller must commit claims before dispatching jobs to workers.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(DISCORD_SYNC_QUEUE_SPEC)

    async def claim(self, *, limit: int) -> tuple[ReconciliationJob, ...]:
        self._session()
        rows = await self._queue.claim(limit=limit, session=self._session())
        return tuple(_job(row, self._queue.token_of(row)) for row in rows)

    async def complete(self, job: ReconciliationJob) -> bool:
        self._session()
        outcome = await self._queue.complete(self._identity(job), job.claim_token, session=self._session())
        return outcome.applied

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        self._session()
        outcome = await self._queue.fail(
            self._identity(job),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
            session=self._session(),
        )
        return outcome.dead_lettered

    @staticmethod
    def _identity(job: ReconciliationJob) -> tuple[ColumnElement[bool], ...]:
        return (DiscordSyncQueueItem.id == job.id,)
