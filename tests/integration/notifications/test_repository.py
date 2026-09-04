"""Integration coverage for notification opt-ins and fenced DM delivery."""

from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventRecord
from squid.notifications.domain import InboxVisibility, NotificationKind, RecordSubscriptionFilter, SubscriptionKind
from squid.notifications.infrastructure.models import (
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationRecord,
    NotificationSubscriptionRecord,
)
from squid.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.persistence.base import Base

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    # Replaced `global_administrators` when permissions moved to RBAC; the
    # notification repository joins it to find who may be notified.
    Base.metadata.tables["permission_roles"],
    Base.metadata.tables["permission_role_assignments"],
    Base.metadata.tables["domain_event_consumers"],
    Base.metadata.tables["domain_events"],
    Base.metadata.tables["domain_event_deliveries"],
    Base.metadata.tables["notification_profiles"],
    Base.metadata.tables["notification_subscriptions"],
    Base.metadata.tables["notifications"],
    Base.metadata.tables["notification_deliveries"],
]


@pytest.fixture
async def notification_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
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


async def _seed_delivery(session_factory: async_sessionmaker[AsyncSession], *, consented: bool = True) -> int:
    async with session_factory.begin() as session:
        account = Account(
            consent_version=CURRENT_CONSENT_VERSION if consented else None,
            consented_at=Instant.now() if consented else None,
        )
        session.add(account)
        await session.flush()
        # The claim reads the DM address from here rather than from the delivery row.
        session.add(AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject="123"))
        session.add(
            NotificationProfile(
                account_id=account.id,
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
            account_id=account.id,
            event_id=event.id,
            source_key="event:1:account:1",
            kind="build_confirmed",
            payload={"build_id": 42},
            web_visible=True,
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDeliveryRecord(notification_id=notification.id, account_id=account.id)
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
        account_id = (await session.scalars(select(NotificationProfile.account_id))).one()

    preferences = await repository.update_preferences(account_id, web_enabled=True, dm_enabled=False)

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


async def test_read_state_changes_are_idempotent_and_visibility_safe(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    async with async_session_factory.begin() as session:
        account_id = (await session.scalars(select(NotificationProfile.account_id))).one()
        ordinary_id = (await session.scalars(select(NotificationRecord.id))).one()
        event = DomainEventRecord(event_type="build.submitted", aggregate_kind="build", aggregate_id=43)
        session.add(event)
        await session.flush()
        staff = NotificationRecord(
            account_id=account_id,
            event_id=event.id,
            source_key="event:2:staff:1",
            kind=NotificationKind.STAFF_BUILD_SUBMITTED,
            payload={"build_id": 43},
            web_visible=True,
        )
        session.add(staff)
        await session.flush()
        staff_id = staff.id

    ordinary = InboxVisibility()
    staff = InboxVisibility(include_staff=True)
    assert await repository.mark_read(account_id, ordinary_id, visibility=ordinary) is True
    assert await repository.mark_read(account_id, ordinary_id, visibility=ordinary) is True
    assert await repository.mark_unread(account_id, ordinary_id, visibility=ordinary) is True
    assert await repository.mark_unread(account_id, ordinary_id, visibility=ordinary) is True
    assert await repository.mark_read(account_id, staff_id, visibility=ordinary) is False
    assert await repository.mark_read(account_id + 1, ordinary_id, visibility=staff) is False
    assert await repository.mark_read(account_id, staff_id, visibility=staff) is True


async def test_cleanup_removes_expired_inbox_and_unreferenced_source_event(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    expired_at = Instant.now().subtract(hours=24 * 91)
    async with async_session_factory.begin() as session:
        await session.execute(cast(Table, NotificationRecord.__table__).update().values(created_at=expired_at))
        await session.execute(cast(Table, DomainEventRecord.__table__).update().values(occurred_at=expired_at))

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
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add(account)
        await session.flush()
        account_id = account.id

    first = await repository.add_subscription(
        account_id,
        kind=SubscriptionKind.CREATOR,
        subject_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_filter=None,
    )
    second = await repository.add_subscription(
        account_id,
        kind=SubscriptionKind.CREATOR,
        subject_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_filter=None,
    )
    filtered = await repository.add_subscription(
        account_id,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"door"})),
    )
    filtered_again = await repository.add_subscription(
        account_id,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"door"})),
    )

    assert first.id == second.id
    assert filtered.id == filtered_again.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(NotificationSubscriptionRecord))).all()) == 2


async def test_an_unconsented_account_keeps_its_switches_but_receives_nothing(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The gate moved onto `accounts`; it did not disappear.

    Folding the notification notice left `dm_enabled = true` rows behind whose accounts have not
    accepted the current privacy notice. The switch survives, because it is the person's stated
    preference, but nothing may be delivered against it until the notice is accepted -- which is
    the whole behavioural claim of the migration that dropped the second receipt.

    Seeded through the same helper as the passing case, so the only difference between a delivery
    that is claimed and one that is not is the account's consent.
    """
    await _seed_delivery(async_session_factory, consented=False)

    assert await repository.claim_deliveries(limit=10) == ()


async def test_a_consented_account_can_set_channels_without_a_separate_accept_step(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """There is no profile row to create first: accepting the notice is the only precondition."""
    async with async_session_factory.begin() as session:
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add(account)
        await session.flush()
        account_id = account.id

    preferences = await repository.update_preferences(account_id, web_enabled=True, dm_enabled=False)

    assert preferences is not None
    assert preferences.web_enabled is True
    assert preferences.dm_enabled is False
    assert preferences.consent_pending is False


async def test_staff_fan_out_inserts_one_row_per_eligible_recipient(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The batched fan-out honours the same per-account gates the single insert did."""
    async with async_session_factory.begin() as session:
        accounts: dict[str, int] = {}
        for name, consented, web, dm in (
            ("both", True, True, True),
            ("web_only", True, True, False),
            ("unconsented", False, True, True),
            ("opted_out", True, False, False),
        ):
            account = Account(
                consent_version=CURRENT_CONSENT_VERSION if consented else None,
                consented_at=Instant.now() if consented else None,
            )
            session.add(account)
            await session.flush()
            session.add(
                AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject=f"discord-{name}")
            )
            session.add(NotificationProfile(account_id=account.id, web_enabled=web, dm_enabled=dm))
            accounts[name] = account.id
        record = DomainEventRecord(event_type="build.submitted", aggregate_kind="build", aggregate_id=7)
        session.add(record)
        await session.flush()
        event = DomainEvent(
            id=record.id,
            event_type="build.submitted",
            aggregate_kind="build",
            aggregate_id=7,
            occurred_at=Instant.now(),
        )

        await repository._insert_many(  # pyright: ignore[reportPrivateUsage]
            session,
            event=event,
            account_ids=sorted(accounts.values()),
            kind=NotificationKind.STAFF_BUILD_SUBMITTED,
            source_key=lambda account_id: f"event:{event.id}:staff:{account_id}",
            payload={"build_id": 7},
        )

    async with async_session_factory() as session:
        notified = set((await session.scalars(select(NotificationRecord.account_id))).all())
        delivered = set((await session.scalars(select(NotificationDeliveryRecord.account_id))).all())

    assert notified == {accounts["both"], accounts["web_only"]}
    assert delivered == {accounts["both"]}
