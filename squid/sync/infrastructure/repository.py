"""PostgreSQL Discord reconciliation queue adapter."""

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.sync.application import ResourceKind, SyncAction, SyncJob
from squid.sync.infrastructure.models import DiscordSyncQueueItem


class PostgresDiscordSyncQueue:
    """Claim coalesced reconciliation work with crash-safe claim tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim(self, *, limit: int) -> tuple[SyncJob, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(DiscordSyncQueueItem)
                        .where(
                            DiscordSyncQueueItem.enqueued_at <= func.now(),
                            or_(
                                DiscordSyncQueueItem.claimed_at.is_(None),
                                DiscordSyncQueueItem.claimed_at < func.now() - text("interval '5 minutes'"),
                            ),
                        )
                        .order_by(DiscordSyncQueueItem.enqueued_at, DiscordSyncQueueItem.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed_at = Instant.now()
            for row in rows:
                row.claimed_at = claimed_at
            await session.commit()
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
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(DiscordSyncQueueItem).where(
                        DiscordSyncQueueItem.id == job.id,
                        DiscordSyncQueueItem.claimed_at == job.claimed_at,
                    )
                ),
            )
            await session.commit()
            return bool(result.rowcount)

    async def fail(self, job: SyncJob, error: str, *, max_attempts: int) -> bool:
        attempts = job.attempts + 1
        async with self._session_factory() as session:
            claim_filter = (
                DiscordSyncQueueItem.id == job.id,
                DiscordSyncQueueItem.claimed_at == job.claimed_at,
            )
            if attempts >= max_attempts:
                result = cast(
                    CursorResult[Any],
                    await session.execute(delete(DiscordSyncQueueItem).where(*claim_filter)),
                )
                await session.commit()
                return bool(result.rowcount)
            delay_seconds = min(15 * 2 ** (attempts - 1), 3600)
            await session.execute(
                update(DiscordSyncQueueItem)
                .where(*claim_filter)
                .values(
                    attempts=attempts,
                    claimed_at=None,
                    enqueued_at=func.now() + timedelta(seconds=delay_seconds),
                    last_error=error[:4000],
                )
            )
            await session.commit()
            return False
