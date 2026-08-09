"""PostgreSQL adapter for durable build-render projections."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue
from squid.schematics.application.render_jobs import ClaimedRenderJob
from squid.schematics.infrastructure.models import SchematicRenderQueueItem


class PostgresSchematicRenderJobRepository:
    """Claim-fenced queue adapter for render enrichment."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(
            session_factory,
            SchematicRenderQueueItem,
            ready_at=SchematicRenderQueueItem.enqueued_at,
            claimed_at=SchematicRenderQueueItem.claimed_at,
            dead_at=SchematicRenderQueueItem.dead_at,
        )

    async def claim(self, *, limit: int) -> tuple[ClaimedRenderJob, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(SchematicRenderQueueItem)
                        .where(
                            SchematicRenderQueueItem.enqueued_at <= func.now(),
                            self._queue.reclaimable(),
                        )
                        .order_by(SchematicRenderQueueItem.enqueued_at, SchematicRenderQueueItem.build_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed_at = await self._queue.stamp(rows, session)
        return tuple(ClaimedRenderJob(row.build_id, row.attempts, claimed_at) for row in rows)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        return await self._queue.complete((SchematicRenderQueueItem.build_id == job.build_id,), job.claimed_at)

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool:
        return await self._queue.fail(
            (SchematicRenderQueueItem.build_id == job.build_id,),
            job.claimed_at,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )
