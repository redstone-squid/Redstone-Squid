"""Database-derived health metrics for durable work queues.

The union below is generated from the same `QueueSpec` constants the claim path
uses, so the readiness predicate is one Python expression rather than eight
hand-written copies. The copies were the defect: the raw SQL in this module wrote
`dead_at IS NULL AND (claimed_at IS NULL OR claimed_at < now() - ...)` seven times,
and nothing made them agree with the adapters or with each other.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, extract, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.events.infrastructure.repository import DOMAIN_EVENT_DELIVERY_SPEC
from squid.observability import record_gauge
from squid.persistence.queue import VISIBILITY_TIMEOUT, ClaimedRowQueue, QueueSpec
from squid.records.infrastructure.repository import RECORD_RECOMPUTE_QUEUE_SPEC
from squid.schematics.infrastructure.jobs import SCHEMATIC_JOB_SPEC
from squid.schematics.infrastructure.render_jobs import SCHEMATIC_RENDER_QUEUE_SPEC
from squid.search.infrastructure.embeddings import SEARCH_EMBEDDING_QUEUE_SPEC
from squid.search.infrastructure.projection import SEARCH_PROJECTION_QUEUE_SPEC
from squid.sync.infrastructure.repository import DISCORD_SYNC_QUEUE_SPEC

VISIBILITY_TIMEOUT_SECONDS = int(VISIBILITY_TIMEOUT.total_seconds())

QUEUE_SPECS: tuple[QueueSpec[Any], ...] = (
    DISCORD_SYNC_QUEUE_SPEC,
    DOMAIN_EVENT_DELIVERY_SPEC,
    RECORD_RECOMPUTE_QUEUE_SPEC,
    SCHEMATIC_JOB_SPEC,
    SCHEMATIC_RENDER_QUEUE_SPEC,
    SEARCH_EMBEDDING_QUEUE_SPEC,
    SEARCH_PROJECTION_QUEUE_SPEC,
)
"""Every durable work queue in the application, in label order."""


def _queue_health_select(spec: QueueSpec[Any]) -> Select[Any]:
    """Count one queue's ready, in-flight and dead rows, and its oldest ready age."""
    queue = ClaimedRowQueue(spec)
    ready = queue.ready()
    claimed_at = spec.claimed_at
    in_flight_conditions = [
        claimed_at.is_not(None),
        claimed_at >= func.now() - VISIBILITY_TIMEOUT,
    ]
    if spec.dead_at is not None:
        in_flight_conditions.append(spec.dead_at.is_(None))
    if spec.pending is not None:
        in_flight_conditions.append(spec.pending)

    shape = spec.health
    # Domain events count per registered consumer through an outer join, so a
    # consumer with no outstanding rows still reports zero instead of vanishing.
    counted = shape.counted if shape is not None and shape.counted is not None else literal(1)
    label = shape.label if shape is not None else literal(spec.name)

    statement = select(
        label.label("queue"),
        func.count(counted).filter(ready).label("ready"),
        func.count(counted).filter(*in_flight_conditions).label("in_flight"),
        (
            func.count(counted).filter(spec.dead_at.is_not(None))
            if spec.dead_at is not None
            # A queue with no dead-letter state reports a constant zero rather than
            # dropping the series, so the gauge stays comparable across queues.
            else literal(0)
        ).label("dead_letters"),
        extract("epoch", func.now() - func.min(spec.available_at).filter(ready)).label("oldest_ready_age"),
    )
    if shape is not None:
        statement = statement.select_from(shape.source).group_by(*shape.group_by)
    else:
        statement = statement.select_from(spec.model)
    return statement


QUEUE_HEALTH_STATEMENT = union_all(*(_queue_health_select(spec) for spec in QUEUE_SPECS))
"""One read-only round trip covering every queue."""


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
            rows = (await session.execute(QUEUE_HEALTH_STATEMENT)).mappings().all()
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
    """Export one queue snapshot through OpenTelemetry gauges any collector can read."""
    attributes = {"squid.queue.name": snapshot.queue}
    record_gauge("squid.queue.ready", snapshot.ready, attributes=attributes)
    record_gauge("squid.queue.in_flight", snapshot.in_flight, attributes=attributes)
    record_gauge("squid.queue.dead_letters", snapshot.dead_letters, attributes=attributes)
    record_gauge("squid.queue.oldest_ready_age", snapshot.oldest_ready_age, attributes=attributes)
