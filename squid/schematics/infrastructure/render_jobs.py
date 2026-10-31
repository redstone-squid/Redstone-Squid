"""PostgreSQL adapter for durable build-preview publication intents."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.schematics.application.render_jobs import ClaimedRenderJob
from squid.schematics.infrastructure.models import SchematicRenderQueueItem

SCHEMATIC_RENDER_QUEUE_SPEC = QueueSpec(
    name="schematic_renders",
    model=SchematicRenderQueueItem,
    key=(SchematicRenderQueueItem.build_id,),
    available_at=SchematicRenderQueueItem.available_at,
    enqueued_at=SchematicRenderQueueItem.enqueued_at,
    claimed_at=SchematicRenderQueueItem.claimed_at,
    claim_token=SchematicRenderQueueItem.claim_token,
    attempts=SchematicRenderQueueItem.attempts,
    last_error=SchematicRenderQueueItem.last_error,
    dead_at=SchematicRenderQueueItem.dead_at,
)


class PostgresSchematicRenderJobRepository:
    """Claim-fenced queue adapter for render enrichment."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._queue = ClaimedRowQueue(SCHEMATIC_RENDER_QUEUE_SPEC, session_factory)

    async def claim(self, *, limit: int) -> tuple[ClaimedRenderJob, ...]:
        rows = await self._queue.claim(limit=limit)
        return tuple(ClaimedRenderJob(row.build_id, row.attempts, self._queue.token_of(row)) for row in rows)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        outcome = await self._queue.complete(
            (SchematicRenderQueueItem.build_id == job.build_id,),
            job.claim_token,
        )
        return outcome.applied

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool:
        outcome = await self._queue.fail(
            (SchematicRenderQueueItem.build_id == job.build_id,),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )
        return outcome.dead_lettered
