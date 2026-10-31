"""PostgreSQL Discord reconciliation queue adapter."""

import uuid

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import DataIntegrityError
from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.sync.application import ReconciliationAction, ReconciliationJob, ReconciliationResource
from squid.sync.infrastructure.models import DiscordSyncQueueItem

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
    """Map a claimed row, refusing values its check constraints should have rejected.

    These two columns used to be `cast()` into their types, so a row that escaped
    its constraint reached the reconciler looking like a valid job and failed
    somewhere else entirely. The constraints stay: they are the reason a bad value
    here is a data-integrity failure rather than a validation error.
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
    """Claim coalesced reconciliation work with crash-safe claim tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._queue = ClaimedRowQueue(DISCORD_SYNC_QUEUE_SPEC, session_factory)

    async def claim(self, *, limit: int) -> tuple[ReconciliationJob, ...]:
        rows = await self._queue.claim(limit=limit)
        return tuple(_job(row, self._queue.token_of(row)) for row in rows)

    async def complete(self, job: ReconciliationJob) -> bool:
        outcome = await self._queue.complete(self._identity(job), job.claim_token)
        return outcome.applied

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        outcome = await self._queue.fail(
            self._identity(job),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )
        return outcome.dead_lettered

    @staticmethod
    def _identity(job: ReconciliationJob) -> tuple[ColumnElement[bool], ...]:
        return (DiscordSyncQueueItem.id == job.id,)
