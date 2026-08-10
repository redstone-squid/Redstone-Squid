"""Integration coverage for notification opt-ins and fenced DM delivery."""

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.events.infrastructure.models import DomainEventRecord
from squid.notifications.domain import CURRENT_NOTIFICATION_NOTICE_VERSION, RecordSubscriptionFilter, SubscriptionKind
from squid.notifications.infrastructure.models import (
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationRecord,
    NotificationSubscriptionRecord,
)
from squid.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.persistence.base import Base
from squid.users.infrastructure.models import User

_TABLES = [
    Base.metadata.tables["users"],
    Base.metadata.tables["global_administrators"],
    Base.metadata.tables["domain_event_consumers"],
    Base.metadata.tables["domain_events"],
    Base.metadata.tables["domain_event_deliveries"],
    Base.metadata.tables["notification_profiles"],
    Base.metadata.tables["notification_subscriptions"],
    Base.metadata.tables["notifications"],
    Base.metadata.tables["notification_deliveries"],
]


@pytest.fixture
async def notification_tables(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.fixture
def repository(
    notification_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> PostgresNotificationRepository:
    return PostgresNotificationRepository(async_session_factory)


async def _seed_delivery(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory.begin() as session:
        user = User(discord_id=123)
        session.add(user)
        await session.flush()
        session.add(
            NotificationProfile(
                user_id=user.id,
                notice_version=CURRENT_NOTIFICATION_NOTICE_VERSION,
                consented_at=Instant.now(),
                web_enabled=True,
                dm_enabled=True,
            )
        )
        event = DomainEventRecord(
            event_type="build.confirmed",
            aggregate_kind="build",
            aggregate_id=42,
        )
        session.add(event)
        await session.flush()
        notification = NotificationRecord(
            user_id=user.id,
            event_id=event.id,
            source_key="event:1:user:1",
            kind="build_confirmed",
            payload={"build_id": 42},
            web_visible=True,
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDeliveryRecord(
            notification_id=notification.id,
            user_id=user.id,
            discord_id=123,
        )
        session.add(delivery)
        await session.flush()
        return delivery.id


async def test_delivery_claims_use_uuid_fencing_and_database_attempt_counts(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await _seed_delivery(async_session_factory)

    claimed = await repository.claim_deliveries(limit=10)

    assert len(claimed) == 1
    assert claimed[0].id == delivery_id
    assert claimed[0].attempts == 1
    assert claimed[0].claim_token is not None
    assert await repository.complete_delivery(claimed[0]) is True
    assert await repository.complete_delivery(claimed[0]) is False


async def test_forbidden_delivery_suspends_only_the_dm_channel(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    delivery = (await repository.claim_deliveries(limit=1))[0]

    assert await repository.suspend_dm(delivery, "forbidden") is True

    async with async_session_factory() as session:
        profile = await session.scalar(select(NotificationProfile))
        assert profile is not None
        assert profile.web_enabled is True
        assert profile.dm_enabled is False
        assert profile.dm_suspended_at is not None


async def test_disabling_dms_cancels_pending_deliveries_without_hiding_the_inbox(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    async with async_session_factory() as session:
        user_id = (await session.scalars(select(NotificationProfile.user_id))).one()

    preferences = await repository.update_preferences(user_id, web_enabled=True, dm_enabled=False)

    assert preferences is not None
    assert preferences.web_enabled is True
    assert preferences.dm_enabled is False
    async with async_session_factory() as session:
        delivery = await session.scalar(select(NotificationDeliveryRecord))
        notification = await session.scalar(select(NotificationRecord))
        assert delivery is not None
        assert delivery.dead_at is not None
        assert notification is not None
        assert notification.web_visible is True


async def test_cleanup_removes_expired_inbox_and_unreferenced_source_event(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    expired_at = Instant.now().subtract(hours=24 * 91)
    async with async_session_factory.begin() as session:
        await session.execute(NotificationRecord.__table__.update().values(created_at=expired_at))
        await session.execute(DomainEventRecord.__table__.update().values(occurred_at=expired_at))

    assert await repository.cleanup(retention_days=90) == 1

    async with async_session_factory() as session:
        assert await session.scalar(select(NotificationRecord)) is None
        assert await session.scalar(select(NotificationDeliveryRecord)) is None
        assert await session.scalar(select(DomainEventRecord)) is None


async def test_equivalent_subscriptions_are_idempotent_at_the_database_boundary(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        user = User(discord_id=456)
        session.add(user)
        await session.flush()
        user_id = user.id

    first = await repository.add_subscription(
        user_id,
        kind=SubscriptionKind.CREATOR,
        subject_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_filter=None,
    )
    second = await repository.add_subscription(
        user_id,
        kind=SubscriptionKind.CREATOR,
        subject_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_filter=None,
    )
    filtered = await repository.add_subscription(
        user_id,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"door"})),
    )
    filtered_again = await repository.add_subscription(
        user_id,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"door"})),
    )

    assert first.id == second.id
    assert filtered.id == filtered_again.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(NotificationSubscriptionRecord))).all()) == 2
