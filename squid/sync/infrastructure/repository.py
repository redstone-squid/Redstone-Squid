"""PostgreSQL Discord reconciliation queue adapter."""

from typing import cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue
from squid.sync.application import ResourceKind, SyncAction, SyncJob
from squid.sync.infrastructure.models import DiscordSyncQueueItem


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

    async def claim(self, *, limit: int) -> tuple[SyncJob, ...]:
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
            return tuple(
                SyncJob(
                    id=row.id,
                    resource_kind=cast(ResourceKind, row.resource_kind),
                    source_key=row.source_key,
                    action=cast(SyncAction, row.action),
                    attempts=row.attempts,
                    claimed_at=claimed_at,
                )
                for row in rows
            )

    async def complete(self, job: SyncJob) -> bool:
        return await self._queue.complete(self._identity(job), job.claimed_at)

    async def fail(self, job: SyncJob, error: str, *, max_attempts: int) -> bool:
        return await self._queue.fail(
            self._identity(job),
            job.claimed_at,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )

    @staticmethod
    def _identity(job: SyncJob) -> tuple[ColumnElement[bool], ...]:
        return (DiscordSyncQueueItem.id == job.id,)
