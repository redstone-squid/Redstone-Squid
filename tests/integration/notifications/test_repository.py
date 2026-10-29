"""Integration coverage for notification opt-ins and fenced DM delivery."""

from collections.abc import AsyncGenerator
from dataclasses import replace
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity
from squid.bot.notifications import render_delivery
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventRecord
from squid.notifications.domain import (
    InboxVisibility,
    NotificationCandidate,
    NotificationKind,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.infrastructure.models import (
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationRecord,
    NotificationSubscriptionRecord,
)
from squid.notifications.infrastructure.repository import (
    PostgresNotificationRepository,
    _is_durable_staff_notification_recipient,
)
from squid.permissions.domain import BuiltinRoleKeys
from squid.permissions.infrastructure.models import (
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRolePattern,
)
from squid.persistence.base import Base
from squid.persistence.types import now
from tests.support.database import record_queries

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    # Replaced `global_administrators` when permissions moved to RBAC; the
    # notification repository joins it to find who may be notified.
    Base.metadata.tables["permission_roles"],
    Base.metadata.tables["permission_role_patterns"],
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


async def _seed_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consented: bool = True,
    discord_id: int = 123,
    build_id: int = 42,
) -> int:
    async with session_factory.begin() as session:
        account = Account(
            consent_version=CURRENT_CONSENT_VERSION if consented else None,
            consented_at=Instant.now() if consented else None,
        )
        session.add(account)
        await session.flush()
        # The claim reads the DM address from here rather than from the delivery row.
        session.add(AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject=str(discord_id)))
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
            aggregate_id=build_id,
        )
        session.add(event)
        await session.flush()
        notification = NotificationRecord(
            account_id=account.id,
            event_id=event.id,
            source_key=f"event:{event.id}:account:{account.id}",
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": build_id},
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


async def test_delivery_batch_returns_each_recipients_details_in_one_statement(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    expected = {}
    for discord_id in (123, 456, 789):
        delivery_id = await _seed_delivery(async_session_factory, discord_id=discord_id, build_id=discord_id + 1)
        expected[delivery_id] = (discord_id, {"build_id": discord_id + 1})

    with record_queries(async_session_factory) as statements:
        claimed = await repository.claim_deliveries(limit=10)

    assert len(statements) == 1
    assert {delivery.id: (delivery.discord_id, delivery.payload) for delivery in claimed} == expected
    assert all(delivery.kind is NotificationKind.BUILD_CONFIRMED for delivery in claimed)


async def test_concurrent_delivery_claims_are_disjoint(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    expected = {await _seed_delivery(async_session_factory, discord_id=discord_id) for discord_id in (123, 456, 789)}
    claimed: list[PendingNotificationDelivery] = []

    async def claim_one() -> None:
        claimed.extend(await repository.claim_deliveries(limit=1))

    async with anyio.create_task_group() as tasks:
        for _ in expected:
            tasks.start_soon(claim_one)

    assert len(claimed) == len(expected)
    assert {delivery.id for delivery in claimed} == expected
    assert await repository.claim_deliveries(limit=10) == ()


async def test_delivery_retry_and_terminal_failure_release_the_claim(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await _seed_delivery(async_session_factory)
    (first,) = await repository.claim_deliveries(limit=1)

    assert not await repository.fail_delivery(first, "temporary failure", max_attempts=2)
    async with async_session_factory.begin() as session:
        row = await session.get(NotificationDeliveryRecord, delivery_id)
        assert row is not None
        assert row.available_at > Instant.now()
        assert (row.claimed_at, row.claim_token, row.dead_at) == (None, None, None)
        assert row.last_error == "temporary failure"
        await session.execute(update(NotificationDeliveryRecord).values(available_at=func.now()))

    (second,) = await repository.claim_deliveries(limit=1)
    assert second.attempts == 2
    assert await repository.fail_delivery(second, "x" * 5000, max_attempts=2)
    async with async_session_factory() as session:
        row = await session.get(NotificationDeliveryRecord, delivery_id)
        assert row is not None
        assert row.dead_at is not None
        assert (row.claimed_at, row.claim_token, row.last_error) == (None, None, "x" * 4000)
    assert await repository.claim_deliveries(limit=1) == ()


async def test_stale_delivery_cannot_complete_fail_or_suspend_the_current_claim(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    (current,) = await repository.claim_deliveries(limit=1)
    stale = replace(current, generation=current.generation + 1)

    assert not await repository.complete_delivery(stale)
    assert not await repository.fail_delivery(stale, "stale failure", max_attempts=1)
    assert not await repository.suspend_dm(stale, "stale forbidden")
    async with async_session_factory() as session:
        row = await session.get(NotificationDeliveryRecord, current.id)
        profile = await session.scalar(select(NotificationProfile))
        assert row is not None
        assert profile is not None
        assert row.claim_token == current.claim_token
        assert (row.sent_at, row.dead_at, row.last_error) == (None, None, None)
        assert profile.dm_enabled
    assert await repository.complete_delivery(current)


async def test_delivery_claim_selects_most_recent_discord_identity_and_completes(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await _seed_delivery(async_session_factory)
    async with async_session_factory.begin() as session:
        account_id = (await session.scalars(select(Account.id))).one()
        verified_at = now()
        existing = (await session.scalars(select(AccountIdentity))).one()
        existing.verified_at = verified_at
        session.add(
            AccountIdentity(
                account_id=account_id,
                provider=IdentityProvider.DISCORD,
                subject="456",
                verified_at=verified_at,
            )
        )

    (claimed,) = await repository.claim_deliveries(limit=10)

    assert claimed.id == delivery_id
    assert claimed.discord_id == 456
    assert render_delivery(claimed, None) == "Your build was confirmed."
    assert await repository.complete_delivery(claimed) is True


async def test_persisted_notification_and_subscription_kinds_round_trip_as_enums(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    async with async_session_factory.begin() as session:
        account_id = (await session.scalars(select(Account.id))).one()
    await repository.add_subscription(
        account_id,
        kind=SubscriptionKind.CREATOR,
        subject_id=UUID("11111111-1111-1111-1111-111111111111"),
        record_filter=None,
    )

    async with async_session_factory() as session:
        notification_kind = (await session.scalars(select(NotificationRecord.kind))).one()
        subscription_kind = (await session.scalars(select(NotificationSubscriptionRecord.kind))).one()

    assert notification_kind is NotificationKind.BUILD_CONFIRMED
    assert subscription_kind is SubscriptionKind.CREATOR


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


@pytest.mark.parametrize("gate", ["web", "consent"])
async def test_read_state_changes_require_current_inbox_eligibility(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
    gate: str,
) -> None:
    await _seed_delivery(async_session_factory)
    async with async_session_factory.begin() as session:
        account = (await session.scalars(select(Account))).one()
        notification_id = (await session.scalars(select(NotificationRecord.id))).one()
        if gate == "web":
            profile = (await session.scalars(select(NotificationProfile))).one()
            profile.web_enabled = False
        else:
            account.consent_version = None
            account.consented_at = None

    assert await repository.mark_read(account.id, notification_id, visibility=InboxVisibility()) is False


async def test_inbox_count_and_both_keyset_directions_share_visibility(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add(account)
        await session.flush()
        session.add(NotificationProfile(account_id=account.id, web_enabled=True, dm_enabled=False))
        event = DomainEventRecord(event_type="build.confirmed", aggregate_kind="build", aggregate_id=42)
        session.add(event)
        await session.flush()
        session.add_all(
            NotificationRecord(
                account_id=account.id,
                event_id=event.id,
                source_key=f"event:{event.id}:account:{index}",
                kind=NotificationKind.STAFF_BUILD_SUBMITTED if index == 5 else NotificationKind.BUILD_CONFIRMED,
                payload={"build_id": index},
                web_visible=True,
            )
            for index in range(1, 6)
        )

    ordinary = InboxVisibility()
    newest = await repository.list_inbox(
        account.id,
        offset=0,
        after_id=None,
        before_id=None,
        limit=2,
        visibility=ordinary,
    )
    older = await repository.list_inbox(
        account.id,
        offset=0,
        after_id=newest[-1].id,
        before_id=None,
        limit=2,
        visibility=ordinary,
    )
    newer = await repository.list_inbox(
        account.id,
        offset=0,
        after_id=None,
        before_id=older[0].id,
        limit=2,
        visibility=ordinary,
    )

    assert [item.payload["build_id"] for item in newest] == [4, 3]
    assert [item.payload["build_id"] for item in older] == [2, 1]
    assert [item.payload["build_id"] for item in newer] == [4, 3]
    assert await repository.count_inbox(account.id, visibility=ordinary) == 4
    assert await repository.count_inbox(account.id, visibility=InboxVisibility(include_staff=True)) == 5


async def test_durable_staff_audience_is_not_implicit_for_owner_or_custom_role_readers(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del repository
    async with async_session_factory.begin() as session:
        global_admin = PermissionRole(
            slug="global-admin",
            name="Global administrator",
            builtin_key=BuiltinRoleKeys.GLOBAL_ADMIN.value,
        )
        owner = PermissionRole(slug="owner", name="Owner", builtin_key=BuiltinRoleKeys.OWNER.value)
        custom = PermissionRole(slug="submission-reader", name="Submission reader")
        session.add_all((global_admin, owner, custom))
        accounts = [Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now()) for _ in range(3)]
        session.add_all(accounts)
        await session.flush()
        session.add(
            PermissionRolePattern(
                role_id=custom.id,
                pattern="build.submission.view_pending",
                mode=1,
            )
        )
        session.add_all(
            PermissionRoleAssignment(role_id=role.id, subject_account_id=account.id)
            for role, account in zip((global_admin, owner, custom), accounts, strict=True)
        )

    async with async_session_factory() as session:
        recipients = tuple(
            (
                await session.scalars(
                    select(Account.id).where(_is_durable_staff_notification_recipient(Account.id)).order_by(Account.id)
                )
            ).all()
        )

    assert recipients == (accounts[0].id,)


@pytest.mark.parametrize("recipient_count", [0, 1, 100])
async def test_durable_staff_audience_discovery_has_constant_query_count(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
    recipient_count: int,
) -> None:
    del repository
    async with async_session_factory.begin() as session:
        role = PermissionRole(
            slug="global-admin",
            name="Global administrator",
            builtin_key=BuiltinRoleKeys.GLOBAL_ADMIN.value,
        )
        session.add(role)
        accounts = [
            Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now()) for _ in range(recipient_count)
        ]
        session.add_all(accounts)
        await session.flush()
        session.add_all(
            PermissionRoleAssignment(role_id=role.id, subject_account_id=account.id) for account in accounts
        )

    with record_queries(async_session_factory) as statements:
        async with async_session_factory() as session:
            recipients = tuple(
                (
                    await session.scalars(
                        select(Account.id)
                        .where(_is_durable_staff_notification_recipient(Account.id))
                        .order_by(Account.id)
                    )
                ).all()
            )

    assert len(recipients) == recipient_count
    assert len(statements) == 1


async def test_cleanup_removes_expired_inbox_and_unreferenced_source_event(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    expired_at = Instant.now().subtract(hours=24 * 91)
    async with async_session_factory.begin() as session:
        await session.execute(update(NotificationRecord).values(created_at=expired_at))
        await session.execute(update(DomainEventRecord).values(occurred_at=expired_at))

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


@pytest.mark.parametrize(("recipient_count", "expected_statements"), [(0, 0), (1, 3), (100, 3)])
async def test_candidate_materialization_has_constant_query_count(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
    recipient_count: int,
    expected_statements: int,
) -> None:
    async with async_session_factory.begin() as session:
        accounts = [
            Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now()) for _ in range(recipient_count)
        ]
        session.add_all(accounts)
        await session.flush()
        for index, account in enumerate(accounts, start=1):
            session.add(AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject=str(index)))
            session.add(NotificationProfile(account_id=account.id, web_enabled=True, dm_enabled=True))
        event = DomainEventRecord(event_type="build.confirmed", aggregate_kind="build", aggregate_id=42)
        session.add(event)
        await session.flush()
        materialized_event = DomainEvent(
            event.id, event.event_type, event.aggregate_kind, event.aggregate_id, Instant.now()
        )
        candidates = tuple(
            NotificationCandidate(
                account_id=account.id,
                source_key=f"event:{event.id}:account:{account.id}",
                kind=NotificationKind.BUILD_CONFIRMED,
                payload={"build_id": 42},
            )
            for account in accounts
        )

    with record_queries(async_session_factory) as statements:
        async with async_session_factory.begin() as session:
            await repository._insert_candidates(session, event=materialized_event, candidates=candidates)

    assert len(statements) == expected_statements


async def test_candidate_materialization_is_unique_under_concurrent_replay(
    repository: PostgresNotificationRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add(account)
        await session.flush()
        session.add(AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject="123"))
        session.add(NotificationProfile(account_id=account.id, web_enabled=True, dm_enabled=True))
        event = DomainEventRecord(event_type="build.confirmed", aggregate_kind="build", aggregate_id=42)
        session.add(event)
        await session.flush()
        materialized_event = DomainEvent(
            event.id,
            event.event_type,
            event.aggregate_kind,
            event.aggregate_id,
            Instant.now(),
        )
        candidate = NotificationCandidate(
            account_id=account.id,
            source_key=f"event:{event.id}:account:{account.id}",
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": 42},
        )

    async def replay() -> None:
        async with async_session_factory.begin() as session:
            await repository._insert_candidates(session, event=materialized_event, candidates=(candidate,))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(replay)
        tasks.start_soon(replay)

    async with async_session_factory() as session:
        assert len((await session.scalars(select(NotificationRecord))).all()) == 1
        assert len((await session.scalars(select(NotificationDeliveryRecord))).all()) == 1


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

        await repository._insert_candidates(  # pyright: ignore[reportPrivateUsage]
            session,
            event=event,
            candidates=tuple(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.STAFF_BUILD_SUBMITTED,
                    source_key=f"event:{event.id}:staff:{account_id}",
                    payload={"build_id": 7},
                )
                for account_id in sorted(accounts.values())
            ),
        )

    async with async_session_factory() as session:
        notified = set((await session.scalars(select(NotificationRecord.account_id))).all())
        delivered = set((await session.scalars(select(NotificationDeliveryRecord.account_id))).all())

    assert notified == {accounts["both"], accounts["web_only"]}
    assert delivered == {accounts["both"]}
