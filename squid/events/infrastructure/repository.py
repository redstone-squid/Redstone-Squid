"""PostgreSQL domain-event delivery adapter."""

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.events.application import DomainEvent, DomainEventDelivery
from squid.events.infrastructure.models import DomainEventDeliveryRecord, DomainEventRecord


class PostgresDomainEventRepository:
    """Claim per-consumer event deliveries with crash-safe claim tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim(self, *, consumer: str, limit: int) -> tuple[DomainEventDelivery, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(DomainEventDeliveryRecord, DomainEventRecord)
                        .join(DomainEventRecord, DomainEventRecord.id == DomainEventDeliveryRecord.event_id)
                        .where(
                            DomainEventDeliveryRecord.consumer == consumer,
                            DomainEventDeliveryRecord.available_at <= func.now(),
                            or_(
                                DomainEventDeliveryRecord.claimed_at.is_(None),
                                DomainEventDeliveryRecord.claimed_at < func.now() - text("interval '5 minutes'"),
                            ),
                        )
                        # Ordered by event id so a consumer observes transitions on one
                        # aggregate in the order they actually happened.
                        .order_by(DomainEventDeliveryRecord.event_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True, of=DomainEventDeliveryRecord)
                    )
                ).all()
            )
            claimed_at = Instant.now()
            for delivery, _ in rows:
                delivery.claimed_at = claimed_at
            await session.commit()
            return tuple(
                DomainEventDelivery(
                    event=DomainEvent(
                        id=event.id,
                        event_type=event.event_type,
                        aggregate_kind=event.aggregate_kind,
                        aggregate_id=event.aggregate_id,
                        occurred_at=event.occurred_at,
                        payload=dict(event.payload),
                    ),
                    consumer=delivery.consumer,
                    attempts=delivery.attempts,
                    claimed_at=claimed_at,
                )
                for delivery, event in rows
            )

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(DomainEventDeliveryRecord).where(*self._claim_filter(delivery))),
            )
            await session.commit()
            return bool(result.rowcount)

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        attempts = delivery.attempts + 1
        async with self._session_factory() as session:
            if attempts >= max_attempts:
                result = cast(
                    CursorResult[Any],
                    await session.execute(delete(DomainEventDeliveryRecord).where(*self._claim_filter(delivery))),
                )
                await session.commit()
                return bool(result.rowcount)
            delay_seconds = min(15 * 2 ** (attempts - 1), 3600)
            await session.execute(
                update(DomainEventDeliveryRecord)
                .where(*self._claim_filter(delivery))
                .values(
                    attempts=attempts,
                    claimed_at=None,
                    available_at=func.now() + timedelta(seconds=delay_seconds),
                    last_error=error[:4000],
                )
            )
            await session.commit()
            return False

    @staticmethod
    def _claim_filter(delivery: DomainEventDelivery) -> tuple[Any, ...]:
        return (
            DomainEventDeliveryRecord.event_id == delivery.event.id,
            DomainEventDeliveryRecord.consumer == delivery.consumer,
            DomainEventDeliveryRecord.claimed_at == delivery.claimed_at,
        )
