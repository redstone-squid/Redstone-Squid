"""PostgreSQL Discord reconciliation queue adapter."""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import DataIntegrityError
from squid.persistence.queue import ClaimedRowQueue
from squid.sync.application import ReconciliationAction, ReconciliationJob, ReconciliationResource
from squid.sync.infrastructure.models import DiscordSyncQueueItem


def _job(row: DiscordSyncQueueItem, claimed_at: Instant) -> ReconciliationJob:
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
        claimed_at=claimed_at,
    )


class PostgresDiscordSyncQueue:
    """Claim coalesced reconciliation work with crash-safe claim tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(
            session_factory,
            DiscordSyncQueueItem,
            ready_at=DiscordSyncQueueItem.enqueued_at,
            claimed_at=DiscordSyncQueueItem.claimed_at,
            dead_at=DiscordSyncQueueItem.dead_at,
        )

    async def claim(self, *, limit: int) -> tuple[ReconciliationJob, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(DiscordSyncQueueItem)
                        .where(
                            DiscordSyncQueueItem.enqueued_at <= func.now(),
                            self._queue.reclaimable(),
                        )
                        .order_by(DiscordSyncQueueItem.enqueued_at, DiscordSyncQueueItem.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed_at = await self._queue.stamp(rows, session)
            return tuple(_job(row, claimed_at) for row in rows)

    async def complete(self, job: ReconciliationJob) -> bool:
        return await self._queue.complete(self._identity(job), job.claimed_at)

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        return await self._queue.fail(
            self._identity(job),
            job.claimed_at,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )

    @staticmethod
    def _identity(job: ReconciliationJob) -> tuple[ColumnElement[bool], ...]:
        return (DiscordSyncQueueItem.id == job.id,)
