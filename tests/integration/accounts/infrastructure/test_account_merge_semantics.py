"""Transaction-level account merge semantics across persistence contexts."""

import uuid

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, ClaimStatus, IdentityProvider
from squid.accounts.errors import AccountNotFoundError
from squid.accounts.infrastructure.models import Account, AccountIdentity, CreatorAlias, CreatorAliasClaim
from squid.accounts.infrastructure.repository import AccountRepository
from squid.core.errors import DataIntegrityError
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventRecord
from squid.notifications.domain import NotificationCandidate, NotificationKind, SubscriptionKind
from squid.notifications.infrastructure.models import (
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationRecord,
    NotificationSubscriptionRecord,
)
from squid.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.persistence.types import now
from squid.submissions.domain import SubmissionOrigin
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.voting.domain import VoteKind, VoteSessionResult, VoteStatus
from squid.voting.infrastructure.models import Vote, VoteSession


@pytest.mark.parametrize(
    "corruption",
    ["wrong_prefix", "incompatible_collision"],
)
async def test_late_notification_integrity_failure_rolls_back_earlier_merge_phases(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    corruption: str,
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    draft_id = uuid.UUID("f0000000-0000-4000-8000-000000000001")
    seeded_at = Instant.now()

    async with migrated_session_factory.begin() as session:
        session.add(
            SubmissionDraft(
                id=draft_id,
                owner_account_id=absorbed.id,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
                origin=SubmissionOrigin.WEB,
                expires_at=seeded_at.add(days=1, days_assumed_24h_ok=True),
            )
        )
        event = DomainEventRecord(event_type="build.confirmed", aggregate_kind="build", aggregate_id=99)
        session.add(event)
        await session.flush()
        notification = NotificationRecord(
            account_id=absorbed.id,
            event_id=event.id,
            source_key=(
                f"event:{event.id + 999}:owner:{absorbed.id}"
                if corruption == "wrong_prefix"
                else f"event:{event.id}:owner:{absorbed.id}"
            ),
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": 99},
            web_visible=True,
        )
        session.add(notification)
        await session.flush()
        if corruption == "incompatible_collision":
            session.add(
                NotificationRecord(
                    account_id=survivor.id,
                    event_id=event.id,
                    source_key=f"event:{event.id}:owner:{survivor.id}",
                    kind=NotificationKind.BUILD_CONFIRMED,
                    payload={"build_id": 100},
                    web_visible=True,
                )
            )

    with pytest.raises(DataIntegrityError, match=r"source key|incompatible"):
        await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        owner_id = await session.scalar(select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == draft_id))
        remaining_accounts = set(await session.scalars(select(Account.id)))
    assert owner_id == absorbed.id
    assert {survivor.id, absorbed.id} <= remaining_accounts


@pytest.mark.parametrize(
    "profile_state",
    ["healthy_survivor", "healthy_absorbed", "both_suspended_disabled", "disabled_unsuspended"],
)
async def test_profile_merge_preserves_realistic_dm_health_states(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    profile_state: str,
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    suspended_at = Instant.now()
    survivor_healthy = profile_state == "healthy_survivor"
    absorbed_healthy = profile_state == "healthy_absorbed"
    both_suspended = profile_state == "both_suspended_disabled"
    async with migrated_session_factory.begin() as session:
        session.add_all(
            [
                NotificationProfile(
                    account_id=survivor.id,
                    web_enabled=False,
                    dm_enabled=survivor_healthy,
                    dm_suspended_at=suspended_at if both_suspended or absorbed_healthy else None,
                ),
                NotificationProfile(
                    account_id=absorbed.id,
                    web_enabled=False,
                    dm_enabled=absorbed_healthy,
                    dm_suspended_at=suspended_at if both_suspended or survivor_healthy else None,
                ),
            ]
        )

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        profile = await session.get(NotificationProfile, survivor.id)
    assert profile is not None
    assert profile.dm_enabled is (survivor_healthy or absorbed_healthy)
    assert profile.dm_suspended_at == (suspended_at if both_suspended else None)


async def test_profile_merge_inserts_an_absorbed_only_profile_for_the_survivor(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    async with migrated_session_factory.begin() as session:
        session.add(NotificationProfile(account_id=absorbed.id, web_enabled=True, dm_enabled=False))

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        profile = await session.get(NotificationProfile, survivor.id)
        absorbed_profile = await session.get(NotificationProfile, absorbed.id)
    assert profile is not None
    assert profile.web_enabled is True
    assert profile.dm_enabled is False
    assert profile.dm_suspended_at is None
    assert absorbed_profile is None


async def test_no_collision_notification_and_delivery_move_together_at_merge_commit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    async with migrated_session_factory.begin() as session:
        event = DomainEventRecord(event_type="build.confirmed", aggregate_kind="build", aggregate_id=42)
        session.add(event)
        await session.flush()
        notification = NotificationRecord(
            account_id=absorbed.id,
            event_id=event.id,
            source_key=f"event:{event.id}:owner:{absorbed.id}",
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": 42},
            web_visible=True,
        )
        session.add(notification)
        await session.flush()
        session.add(NotificationDeliveryRecord(notification_id=notification.id, account_id=absorbed.id))

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        merged_notification = (await session.scalars(select(NotificationRecord))).one()
        merged_delivery = (await session.scalars(select(NotificationDeliveryRecord))).one()
    assert merged_notification.account_id == survivor.id
    assert merged_notification.source_key == f"event:{event.id}:owner:{survivor.id}"
    assert merged_delivery.notification_id == merged_notification.id
    assert merged_delivery.account_id == survivor.id


async def test_legacy_record_key_merge_replay_and_stale_delivery_completion_are_idempotent(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    async with migrated_session_factory.begin() as session:
        account_rows = tuple(
            (await session.scalars(select(Account).where(Account.id.in_((survivor.id, absorbed.id))))).all()
        )
        for account in account_rows:
            account.consent_version = CURRENT_CONSENT_VERSION
            account.consented_at = Instant.now()
        session.add(
            AccountIdentity(
                account_id=absorbed.id,
                provider=IdentityProvider.DISCORD,
                subject="700000000000000009",
            )
        )
        session.add_all(
            [
                NotificationProfile(account_id=survivor.id, web_enabled=True, dm_enabled=False),
                NotificationProfile(account_id=absorbed.id, web_enabled=True, dm_enabled=True),
            ]
        )
        event = DomainEventRecord(event_type="record_run.activated", aggregate_kind="record_run", aggregate_id=5)
        session.add(event)
        await session.flush()
        notification = NotificationRecord(
            account_id=absorbed.id,
            event_id=event.id,
            source_key=f"event:{event.id}:record-build:42:user:{absorbed.id}",
            kind=NotificationKind.RECORD_GAINED,
            payload={"build_id": 42, "records": []},
            web_visible=True,
        )
        session.add(notification)
        session.add(
            NotificationRecord(
                account_id=survivor.id,
                event_id=event.id,
                source_key=f"event:{event.id}:record-build:42:user:{survivor.id}",
                kind=NotificationKind.RECORD_GAINED,
                payload={"build_id": 42, "records": []},
                web_visible=True,
            )
        )
        await session.flush()
        session.add(NotificationDeliveryRecord(notification_id=notification.id, account_id=absorbed.id))
        event_value = DomainEvent(
            id=event.id,
            event_type=event.event_type,
            aggregate_kind=event.aggregate_kind,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            schema_version=event.schema_version,
        )

    notifications = PostgresNotificationRepository(migrated_session_factory)
    (stale_claim,) = await notifications.claim_deliveries(limit=1)
    await accounts.merge(survivor.id, absorbed.id)
    assert await notifications.complete_delivery(stale_claim) is False
    async with migrated_session_factory.begin() as session:
        await notifications._insert_candidates(
            session,
            event=event_value,
            candidates=(
                NotificationCandidate(
                    account_id=survivor.id,
                    kind=NotificationKind.RECORD_GAINED,
                    source_key=f"event:{event_value.id}:record-build:42:account:{survivor.id}",
                    payload={"build_id": 42, "records": []},
                ),
            ),
        )

    async with migrated_session_factory() as session:
        merged = tuple(
            (await session.scalars(select(NotificationRecord).where(NotificationRecord.event_id == event.id))).all()
        )
        delivery = (await session.scalars(select(NotificationDeliveryRecord))).one()
    assert len(merged) == 1
    assert merged[0].source_key == f"event:{event.id}:record-build:42:account:{survivor.id}"
    assert delivery.account_id == survivor.id
    assert delivery.generation > stale_claim.generation


async def test_simultaneous_reversed_merges_lock_in_one_order_and_choose_one_winner(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    first = await accounts.create()
    second = await accounts.create()
    assert first.id is not None
    assert second.id is not None
    outcomes: list[tuple[str, int]] = []

    async def merge(survivor_id: int, absorbed_id: int) -> None:
        try:
            await accounts.merge(survivor_id, absorbed_id)
        except AccountNotFoundError as exc:
            outcomes.append(("missing", -1 if exc.account_id is None else exc.account_id))
        else:
            outcomes.append(("merged", survivor_id))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(merge, first.id, second.id)
        tasks.start_soon(merge, second.id, first.id)

    assert sorted(kind for kind, _ in outcomes) == ["merged", "missing"]
    async with migrated_session_factory() as session:
        remaining_ids = tuple(await session.scalars(select(Account.id).where(Account.id.in_((first.id, second.id)))))
    assert len(remaining_ids) == 1
    assert outcomes.count(("merged", remaining_ids[0])) == 1


async def test_core_conflict_statements_collapse_claim_vote_and_subscription_collisions(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    subject_id = uuid.UUID("f0000000-0000-4000-8000-000000000002")
    disabled_subject_id = uuid.UUID("f0000000-0000-4000-8000-000000000003")
    resolved_at = now()
    record_filter: dict[str, object] = {"build_kinds": ["door"]}
    async with migrated_session_factory.begin() as session:
        alias = CreatorAlias(name="Merge Builder")
        vote_session = VoteSession(
            status=VoteStatus.OPEN,
            result=VoteSessionResult.PENDING,
            author_account_id=survivor.id,
            kind=VoteKind.GENERIC,
            pass_threshold=None,
            fail_threshold=None,
        )
        session.add_all([alias, vote_session])
        await session.flush()
        session.add_all(
            [
                CreatorAliasClaim(alias_id=alias.id, account_id=survivor.id),
                CreatorAliasClaim(alias_id=alias.id, account_id=absorbed.id),
                CreatorAliasClaim(
                    alias_id=alias.id,
                    account_id=survivor.id,
                    status=ClaimStatus.APPROVED,
                    resolved_at=resolved_at,
                ),
                CreatorAliasClaim(
                    alias_id=alias.id,
                    account_id=absorbed.id,
                    status=ClaimStatus.REJECTED,
                    resolved_at=resolved_at,
                ),
                Vote(
                    vote_session_id=vote_session.id,
                    account_id=survivor.id,
                    guild_id=1,
                    option_id="yes",
                    emoji="yes",
                    weight=1.0,
                ),
                Vote(
                    vote_session_id=vote_session.id,
                    account_id=absorbed.id,
                    guild_id=1,
                    option_id="no",
                    emoji="no",
                    weight=1.0,
                ),
                NotificationSubscriptionRecord(
                    account_id=survivor.id,
                    kind=SubscriptionKind.CREATOR,
                    subject_id=subject_id,
                ),
                NotificationSubscriptionRecord(
                    account_id=absorbed.id,
                    kind=SubscriptionKind.CREATOR,
                    subject_id=subject_id,
                ),
                NotificationSubscriptionRecord(
                    account_id=survivor.id,
                    kind=SubscriptionKind.CREATOR,
                    subject_id=disabled_subject_id,
                    enabled=False,
                ),
                NotificationSubscriptionRecord(
                    account_id=absorbed.id,
                    kind=SubscriptionKind.CREATOR,
                    subject_id=disabled_subject_id,
                    enabled=False,
                ),
                NotificationSubscriptionRecord(
                    account_id=survivor.id,
                    kind=SubscriptionKind.RECORD_FILTER,
                    filter=record_filter,
                ),
                NotificationSubscriptionRecord(
                    account_id=absorbed.id,
                    kind=SubscriptionKind.RECORD_FILTER,
                    filter=record_filter,
                ),
            ]
        )

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        claims = tuple(
            (
                await session.execute(
                    select(CreatorAliasClaim.account_id, CreatorAliasClaim.status).order_by(CreatorAliasClaim.id)
                )
            ).all()
        )
        vote_accounts = tuple(await session.scalars(select(Vote.account_id)))
        subscriptions = tuple(
            (
                await session.execute(
                    select(
                        NotificationSubscriptionRecord.account_id,
                        NotificationSubscriptionRecord.kind,
                        NotificationSubscriptionRecord.subject_id,
                        NotificationSubscriptionRecord.filter,
                        NotificationSubscriptionRecord.enabled,
                    ).order_by(NotificationSubscriptionRecord.id)
                )
            ).all()
        )
    assert claims == (
        (survivor.id, ClaimStatus.PENDING),
        (survivor.id, ClaimStatus.APPROVED),
        (survivor.id, ClaimStatus.REJECTED),
    )
    assert vote_accounts == (survivor.id,)
    assert len(subscriptions) == 3
    assert all(subscription.account_id == survivor.id for subscription in subscriptions)
    assert {(subscription.kind, subscription.enabled) for subscription in subscriptions} == {
        (SubscriptionKind.CREATOR, True),
        (SubscriptionKind.CREATOR, False),
        (SubscriptionKind.RECORD_FILTER, True),
    }
    record_subscription = next(
        subscription for subscription in subscriptions if subscription.kind is SubscriptionKind.RECORD_FILTER
    )
    assert record_subscription.subject_id is None
    assert record_subscription.filter == record_filter
