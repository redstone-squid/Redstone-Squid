"""Database-derived health metrics for durable work queues."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.observability import record_gauge
from squid.persistence.queue import VISIBILITY_TIMEOUT

VISIBILITY_TIMEOUT_SECONDS = int(VISIBILITY_TIMEOUT.total_seconds())
QUEUE_HEALTH_SQL = f"""
SELECT 'discord_sync' AS queue,
       count(*) FILTER (WHERE dead_at IS NULL
           AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
           AND enqueued_at <= now()) AS ready,
       count(*) FILTER (WHERE dead_at IS NULL AND claimed_at IS NOT NULL
           AND claimed_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second') AS in_flight,
       count(*) FILTER (WHERE dead_at IS NOT NULL) AS dead_letters,
       extract(epoch FROM now() - min(enqueued_at) FILTER (
           WHERE dead_at IS NULL
               AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND enqueued_at <= now()
       )) AS oldest_ready_age
FROM discord_sync_queue
UNION ALL
SELECT 'domain_events.' || consumers.name,
       count(deliveries.event_id) FILTER (
           WHERE deliveries.dead_at IS NULL
               AND (deliveries.claimed_at IS NULL
                   OR deliveries.claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND deliveries.available_at <= now()
       ),
       count(deliveries.event_id) FILTER (
           WHERE deliveries.dead_at IS NULL AND deliveries.claimed_at IS NOT NULL
               AND deliveries.claimed_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second'
       ),
       count(deliveries.event_id) FILTER (WHERE deliveries.dead_at IS NOT NULL),
       extract(epoch FROM now() - min(deliveries.available_at) FILTER (
           WHERE deliveries.dead_at IS NULL
               AND (deliveries.claimed_at IS NULL
                   OR deliveries.claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND deliveries.available_at <= now()
       ))
FROM domain_event_consumers AS consumers
LEFT JOIN domain_event_deliveries AS deliveries ON deliveries.consumer = consumers.name
GROUP BY consumers.name
UNION ALL
SELECT 'schematic_jobs',
       count(*) FILTER (
           WHERE completed_at IS NULL AND dead_at IS NULL
               AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND available_at <= now()
       ),
       count(*) FILTER (WHERE completed_at IS NULL AND dead_at IS NULL AND claimed_at IS NOT NULL
           AND claimed_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second'),
       count(*) FILTER (WHERE dead_at IS NOT NULL),
       extract(epoch FROM now() - min(available_at) FILTER (
           WHERE completed_at IS NULL AND dead_at IS NULL
               AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND available_at <= now()
       ))
FROM schematic_jobs
UNION ALL
SELECT 'schematic_renders',
       count(*) FILTER (WHERE dead_at IS NULL
           AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
           AND enqueued_at <= now()),
       count(*) FILTER (WHERE dead_at IS NULL AND claimed_at IS NOT NULL
           AND claimed_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second'),
       count(*) FILTER (WHERE dead_at IS NOT NULL),
       extract(epoch FROM now() - min(enqueued_at) FILTER (
           WHERE dead_at IS NULL
               AND (claimed_at IS NULL OR claimed_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND enqueued_at <= now()
       ))
FROM schematic_render_queue
UNION ALL
SELECT 'search_projections',
       count(*) FILTER (WHERE dead_at IS NULL
           AND (locked_at IS NULL OR locked_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
           AND enqueued_at <= now()),
       count(*) FILTER (WHERE dead_at IS NULL AND locked_at IS NOT NULL
           AND locked_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second'),
       count(*) FILTER (WHERE dead_at IS NOT NULL),
       extract(epoch FROM now() - min(enqueued_at) FILTER (
           WHERE dead_at IS NULL
               AND (locked_at IS NULL OR locked_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND enqueued_at <= now()
       ))
FROM search_projection_queue
UNION ALL
SELECT 'search_embeddings',
       count(*) FILTER (WHERE dead_at IS NULL
           AND (locked_at IS NULL OR locked_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
           AND enqueued_at <= now()),
       count(*) FILTER (WHERE dead_at IS NULL AND locked_at IS NOT NULL
           AND locked_at >= now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second'),
       count(*) FILTER (WHERE dead_at IS NOT NULL),
       extract(epoch FROM now() - min(enqueued_at) FILTER (
           WHERE dead_at IS NULL
               AND (locked_at IS NULL OR locked_at < now() - {VISIBILITY_TIMEOUT_SECONDS} * interval '1 second')
               AND enqueued_at <= now()
       ))
FROM search_embedding_queue
UNION ALL
SELECT 'record_recomputation',
       count(*) FILTER (WHERE locked_at IS NULL),
       count(*) FILTER (WHERE locked_at IS NOT NULL),
       0,
       extract(epoch FROM now() - min(enqueued_at) FILTER (WHERE locked_at IS NULL))
FROM record_recompute_queue
"""


@dataclass(frozen=True, slots=True)
class QueueHealthSnapshot:
    """Low-cardinality operational state for one durable queue."""

    queue: str
    ready: int
    in_flight: int
    dead_letters: int
    oldest_ready_age: float


class PostgresQueueHealthMonitor:
    """Sample all durable queues in one read-only database round trip."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self) -> None:
        async with self._session_factory() as session:
            rows = (await session.execute(text(QUEUE_HEALTH_SQL))).mappings().all()
        for row in rows:
            emit_queue_health(
                QueueHealthSnapshot(
                    queue=str(row["queue"]),
                    ready=int(row["ready"]),
                    in_flight=int(row["in_flight"]),
                    dead_letters=int(row["dead_letters"]),
                    oldest_ready_age=float(row["oldest_ready_age"] or 0.0),
                )
            )


def emit_queue_health(snapshot: QueueHealthSnapshot) -> None:
    """Export one queue snapshot through vendor-neutral OpenTelemetry gauges."""
    attributes = {"squid.queue.name": snapshot.queue}
    record_gauge("squid.queue.ready", snapshot.ready, attributes=attributes)
    record_gauge("squid.queue.in_flight", snapshot.in_flight, attributes=attributes)
    record_gauge("squid.queue.dead_letters", snapshot.dead_letters, attributes=attributes)
    record_gauge("squid.queue.oldest_ready_age", snapshot.oldest_ready_age, attributes=attributes)
