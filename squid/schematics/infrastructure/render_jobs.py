"""PostgreSQL queue for requests to update a build's generated preview."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.schematics.application.render_jobs import ClaimedRenderJob
from squid.schematics.models import SchematicRenderQueueItem

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
    """Persist preview jobs, accepting worker outcomes only while their claim token matches."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(SCHEMATIC_RENDER_QUEUE_SPEC)

    async def claim(self, *, limit: int) -> tuple[ClaimedRenderJob, ...]:
        rows = await self._queue.claim(limit=limit, session=self._session())
        return tuple(ClaimedRenderJob(row.build_id, row.attempts, self._queue.token_of(row)) for row in rows)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        outcome = await self._queue.complete(
            (SchematicRenderQueueItem.build_id == job.build_id,),
            job.claim_token,
            session=self._session(),
        )
        return outcome.applied

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool:
        outcome = await self._queue.fail(
            (SchematicRenderQueueItem.build_id == job.build_id,),
            job.claim_token,
            session=self._session(),
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )
        return outcome.dead_lettered
