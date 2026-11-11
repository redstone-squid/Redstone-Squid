"""PostgreSQL notification persistence and domain-event materialization."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.consent import account_consent_current
from squid.accounts.models import Account, AccountIdentity, CreatorAlias
from squid.builds.domain import Status
from squid.builds.models import Build, BuildCreator
from squid.events.models import DomainEvent, DomainEventDeliveryRecord
from squid.notifications.domain import (
    InboxVisibility,
    NotificationCandidate,
    NotificationKind,
    NotificationPreferences,
    NotificationSource,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.models import (
    InboxNotification,
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationSubscription,
)
from squid.permissions.domain import BuiltinRoleKeys
from squid.permissions.models import PermissionRole, PermissionRoleAssignment
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.records.models import (
    RecordTitle,
    TitleDefinition,
    TitleHolder,
    TitleResult,
)
from squid.tags.models import TagAssignment


def _durable_staff_notification_role_ids():
    """Select the built-in role used to find staff notification recipients.

    Background delivery uses direct global-admin account assignments because it has
    no guild, Discord-role, or request-owner context. Request-time inbox visibility
    separately checks build.submission.view_pending; that permission alone does not
    subscribe an account to staff notifications.
    """
    return select(PermissionRole.id).where(PermissionRole.builtin_key == BuiltinRoleKeys.GLOBAL_ADMIN.value)


def _is_durable_staff_notification_recipient(account_column):
    return exists().where(
        PermissionRoleAssignment.subject_account_id == account_column,
        PermissionRoleAssignment.role_id.in_(_durable_staff_notification_role_ids()),
        PermissionRoleAssignment.scope_guild_id.is_(None),
        or_(
            PermissionRoleAssignment.expires_at.is_(None),
            PermissionRoleAssignment.expires_at > func.now(),
        ),
    )


def _most_recent_discord_identity_id(account_column):
    """Select the newest verified Discord identity, with persistence order breaking ties."""
    return (
        select(AccountIdentity.id)
        .where(
            AccountIdentity.account_id == account_column,
            AccountIdentity.provider == IdentityProvider.DISCORD,
        )
        .order_by(AccountIdentity.verified_at.desc(), AccountIdentity.id.desc())
        .limit(1)
        .correlate(NotificationDeliveryRecord)
        .scalar_subquery()
    )


@dataclass(frozen=True, slots=True)
class _RecordGain:
    title_id: UUID
    build_id: int
    title: str
    base_title: str
    build_kind: str
    version_scope: str


class PostgresNotificationRepository:
    """Store notification preferences and turn domain events into recipient messages.

    Event-specific queries find candidates: build submitters, creator or record
    subscribers, and staff recipients. Candidates that pass consent and channel
    checks become InboxNotification rows. Those rows also hold the message data
    for DMs, including when web delivery is disabled; NotificationDeliveryRecord
    tracks the separate send attempts.

    Source keys prevent an event retry from inserting the same notification twice.
    DM claims recheck current consent and preferences and resolve the recipient's
    current Discord identity. A claim token and account-merge generation ensure
    only the current sender can settle an attempt.

    All methods use the database session from NotificationService's unit of work.
    The service commits each operation; this repository does not send messages.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def lock_settings(self, account_id: int) -> int:
        session = self._session()
        await session.get(Account, account_id, with_for_update=True)
        row = await session.get(NotificationProfile, account_id, with_for_update=True)
        return 0 if row is None else row.revision

    async def advance_settings(self, account_id: int) -> int:
        row = await self._session().scalar(
            insert(NotificationProfile)
            .values(account_id=account_id)
            .on_conflict_do_update(
                index_elements=[NotificationProfile.account_id], set_={"revision": NotificationProfile.revision + 1}
            )
            .returning(NotificationProfile)
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row.revision

    async def subscription(self, account_id: int, subscription_id: int) -> NotificationSubscription | None:
        return await self._session().scalar(
            select(NotificationSubscription).where(
                NotificationSubscription.account_id == account_id, NotificationSubscription.id == subscription_id
            )
        )

    async def matching_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription | None:
        return await self._session().scalar(
            select(NotificationSubscription)
            .where(
                NotificationSubscription.account_id == account_id,
                NotificationSubscription.kind == kind,
                NotificationSubscription.subject_id.is_not_distinct_from(subject_id),
                NotificationSubscription.record_filter.is_not_distinct_from(record_filter),
            )
            .order_by(NotificationSubscription.enabled.desc(), NotificationSubscription.id)
            .limit(1)
        )

    async def reserve_subscription_id(self) -> int:
        result = await self._session().scalar(
            select(func.nextval(func.pg_get_serial_sequence("notification_subscriptions", "id")))
        )
        assert isinstance(result, int)
        return result

    async def save_subscription(self, row: NotificationSubscription) -> None:
        await self.lock_settings(row.account_id)
        self._session().add(row)
        await self._session().flush()

    async def lock_inbox(
        self, account_id: int, ids: tuple[int, ...], *, visibility: InboxVisibility
    ) -> Sequence[InboxNotification]:
        return (
            await self._session().scalars(
                select(InboxNotification)
                .where(InboxNotification.id.in_(ids), *_inbox_predicates(account_id, visibility))
                .order_by(InboxNotification.id)
                .with_for_update()
            )
        ).all()

    async def flush_inbox(self) -> None:
        await self._session().flush()

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        session = self._session()
        profile = await session.get(NotificationProfile, account_id)
        return _preferences(account_id, profile, await self._consent_pending(session, account_id))

    @staticmethod
    async def _consent_pending(session: AsyncSession, account_id: int) -> bool:
        """Whether the account still owes the current privacy notice."""
        writable = await session.scalar(select(Account.id).where(Account.id == account_id, account_consent_current()))
        return writable is None

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None:
        """Set both channels, creating the profile row on first use.

        Return None when the account does not satisfy the consent policy. Enabling
        DMs clears a previous suspension; disabling them terminates pending deliveries.
        """
        session = self._session()
        await session.get(Account, account_id, with_for_update=True)
        if await self._consent_pending(session, account_id):
            return None
        profile = (
            await session.scalars(
                insert(NotificationProfile)
                .values(
                    account_id=account_id,
                    web_enabled=web_enabled,
                    dm_enabled=dm_enabled,
                    dm_suspended_at=None,
                    updated_at=func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[NotificationProfile.account_id],
                    set_={
                        "web_enabled": web_enabled,
                        "dm_enabled": dm_enabled,
                        # Re-enabling DMs clears a suspension; leaving them off preserves it,
                        # so a bounce is not forgotten by an unrelated web-inbox toggle.
                        "dm_suspended_at": None if dm_enabled else NotificationProfile.dm_suspended_at,
                        "updated_at": func.now(),
                        "revision": NotificationProfile.revision + 1,
                    },
                )
                .returning(NotificationProfile)
                .execution_options(populate_existing=True)
            )
        ).one()
        if not dm_enabled:
            await self._cancel_pending_deliveries(session, account_id)
        return _preferences(account_id, profile, consent_pending=False)

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool:
        session = self._session()
        if kind is SubscriptionKind.CREATOR:
            statement = select(
                exists().where(
                    Account.public_creator_id == subject_id,
                    CreatorAlias.account_id == Account.id,
                )
            )
        else:
            statement = select(exists().where(RecordTitle.public_id == subject_id))
        return bool(await session.scalar(statement))

    async def add_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription:
        await self.lock_settings(account_id)
        existing = await self.matching_subscription(
            account_id, kind=kind, subject_id=subject_id, record_filter=record_filter
        )
        if existing is not None and existing.enabled:
            return existing
        if existing is None:
            existing = NotificationSubscription(
                account_id=account_id, kind=kind, subject_id=subject_id, record_filter=record_filter
            )
            self._session().add(existing)
        else:
            existing.enabled = True
        await self.advance_settings(account_id)
        await self._session().flush()
        return existing

    async def list_subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        session = self._session()
        rows = (
            await session.scalars(
                select(NotificationSubscription)
                .where(
                    NotificationSubscription.account_id == account_id,
                    NotificationSubscription.enabled.is_(True),
                )
                .order_by(NotificationSubscription.created_at, NotificationSubscription.id)
            )
        ).all()
        return tuple(row for row in rows)

    async def delete_subscription(self, account_id: int, subscription_id: int) -> bool:
        await self.lock_settings(account_id)
        row = await self.subscription(account_id, subscription_id)
        if row is None or not row.enabled:
            return False
        row.enabled = False
        await self.advance_settings(account_id)
        return True

    async def list_inbox(
        self,
        account_id: int,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        limit: int,
        visibility: InboxVisibility,
    ) -> Sequence[InboxNotification]:
        """List one newest-first inbox page; a `before_id` page carries its overfetch at the front."""
        session = self._session()
        statement = _inbox_filter(select(InboxNotification), account_id=account_id, visibility=visibility)
        reverse = before_id is not None
        if before_id is not None:
            # Walk away from the anchor in reversed display order; the page is flipped back below.
            statement = statement.where(InboxNotification.id > before_id).order_by(InboxNotification.id.asc())
        else:
            if after_id is not None:
                statement = statement.where(InboxNotification.id < after_id)
            statement = statement.order_by(InboxNotification.id.desc())
        if offset:
            statement = statement.offset(offset)
        rows = (await session.scalars(statement.limit(limit))).all()
        ordered = reversed(rows) if reverse else rows
        return tuple(row for row in ordered)

    async def count_inbox(self, account_id: int, *, visibility: InboxVisibility) -> int:
        """Count web-visible inbox items, mirroring the visibility rules of `list_inbox`."""
        session = self._session()
        statement = _inbox_filter(
            select(func.count()).select_from(InboxNotification),
            account_id=account_id,
            visibility=visibility,
        )
        return await session.scalar(statement) or 0

    async def mark_read(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        self._session()
        return await self._set_read_state(account_id, notification_id, visibility=visibility, read=True)

    async def mark_unread(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        self._session()
        return await self._set_read_state(account_id, notification_id, visibility=visibility, read=False)

    async def _set_read_state(
        self,
        account_id: int,
        notification_id: int,
        *,
        visibility: InboxVisibility,
        read: bool,
    ) -> bool:
        session = self._session()
        updated = await session.scalar(
            update(InboxNotification)
            .where(InboxNotification.id == notification_id, *_inbox_predicates(account_id, visibility))
            .values(
                read_at=func.coalesce(InboxNotification.read_at, func.now()) if read else None,
                revision=InboxNotification.revision + 1,
            )
            .returning(InboxNotification.id)
        )
        return updated is not None

    # Create notifications from events and expire retained notifications.

    async def materialize(self, event: DomainEvent) -> None:
        session = self._session()
        if event.event_type == "build.submitted":
            await self._materialize_staff_submission(session, event)
        elif event.event_type in {"build.confirmed", "build.denied"}:
            await self._materialize_build_outcome(session, event)
        elif event.event_type == "record_run.activated":
            await self._materialize_record_gains(session, event)

    async def cleanup(self, *, retention_days: int) -> int:
        session = self._session()
        cutoff = func.now() - func.make_interval(0, 0, 0, retention_days)
        removed_rows = (
            delete(InboxNotification)
            .where(InboxNotification.created_at < cutoff)
            .returning(InboxNotification.id)
            .cte("removed_notifications")
        )
        removed = await session.scalar(select(func.count()).select_from(removed_rows))
        await session.execute(
            delete(DomainEvent).where(
                DomainEvent.occurred_at < cutoff,
                ~exists().where(InboxNotification.event_id == DomainEvent.id),
                ~exists().where(DomainEventDeliveryRecord.event_id == DomainEvent.id),
            )
        )
        return int(removed or 0)

    # DM claims reserve work; completion and failure apply only to the current claim.

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]:
        session = self._session()
        claimable_staff = _is_durable_staff_notification_recipient(NotificationDeliveryRecord.account_id)
        # Resolve the current Discord identity here so unlinking prevents pending
        # DMs from being claimed and relinking uses the new address.
        candidates = (
            select(
                NotificationDeliveryRecord.id,
                AccountIdentity.subject,
                InboxNotification.kind,
                InboxNotification.payload,
            )
            .join(NotificationProfile, NotificationProfile.account_id == NotificationDeliveryRecord.account_id)
            .join(
                InboxNotification,
                (InboxNotification.id == NotificationDeliveryRecord.notification_id)
                & (InboxNotification.account_id == NotificationDeliveryRecord.account_id),
            )
            .join(Account, Account.id == NotificationDeliveryRecord.account_id)
            .join(
                AccountIdentity,
                AccountIdentity.id == _most_recent_discord_identity_id(NotificationDeliveryRecord.account_id),
            )
            .where(
                NotificationDeliveryRecord.available_at <= func.now(),
                NotificationDeliveryRecord.dead_at.is_(None),
                NotificationDeliveryRecord.sent_at.is_(None),
                or_(
                    NotificationDeliveryRecord.claimed_at.is_(None),
                    NotificationDeliveryRecord.claimed_at < func.now() - VISIBILITY_TIMEOUT,
                ),
                account_consent_current(),
                NotificationProfile.dm_enabled.is_(True),
                NotificationProfile.dm_suspended_at.is_(None),
                or_(InboxNotification.kind != NotificationKind.STAFF_BUILD_SUBMITTED, claimable_staff),
            )
            .order_by(NotificationDeliveryRecord.available_at, NotificationDeliveryRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=NotificationDeliveryRecord)
            .cte("claimable_deliveries")
        )
        claimed = await session.execute(
            update(NotificationDeliveryRecord)
            .where(NotificationDeliveryRecord.id == candidates.c.id)
            .values(
                claimed_at=func.now(),
                claim_token=func.gen_random_uuid(),
            )
            .returning(NotificationDeliveryRecord, candidates.c.subject, candidates.c.kind, candidates.c.payload)
        )
        return tuple(
            PendingNotificationDelivery(
                id=delivery.id,
                generation=delivery.generation,
                discord_id=int(subject),
                nonce=delivery.nonce,
                claim_token=_claim_token(delivery),
                attempts=delivery.attempts,
                kind=kind,
                payload=dict(payload),
            )
            for delivery, subject, kind, payload in claimed
        )

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        session = self._session()
        return await self._settle_delivery(session, delivery, sent_at=func.now()) is not None

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool:
        self._session()
        attempts = delivery.attempts + 1
        dead = attempts >= max_attempts
        values = {"dead_at": func.now()} if dead else {"available_at": func.now() + retry_delay(attempts)}
        session = self._session()
        account_id = await self._settle_delivery(session, delivery, error=error, attempts=attempts, **values)
        return dead and account_id is not None

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool:
        session = self._session()
        # Settings changes lock the account before delivery rows. Follow that order,
        # then recheck the claim: an account merge may have moved it while we waited.
        account_id = await session.scalar(
            select(NotificationDeliveryRecord.account_id).where(*_delivery_claim(delivery))
        )
        if account_id is None:
            return False
        await self.lock_settings(account_id)
        account_id = await self._settle_delivery(
            session, delivery, error=error, attempts=delivery.attempts + 1, dead_at=func.now()
        )
        if account_id is None:
            return False
        await session.execute(
            update(NotificationProfile)
            .where(NotificationProfile.account_id == account_id)
            .values(
                dm_enabled=False,
                dm_suspended_at=func.now(),
                updated_at=func.now(),
                revision=NotificationProfile.revision + 1,
            )
        )
        return True

    @staticmethod
    async def _settle_delivery(
        session: AsyncSession, delivery: PendingNotificationDelivery, *, error: str | None = None, **values: Any
    ) -> int | None:
        """Release only the current claim and return its owner when the transition applies."""
        return await session.scalar(
            update(NotificationDeliveryRecord)
            .where(*_delivery_claim(delivery))
            .values(claimed_at=None, claim_token=None, last_error=None if error is None else error[:4000], **values)
            .returning(NotificationDeliveryRecord.account_id)
        )

    @staticmethod
    async def _cancel_pending_deliveries(session: AsyncSession, account_id: int) -> None:
        await session.execute(
            update(NotificationDeliveryRecord)
            .where(
                NotificationDeliveryRecord.account_id == account_id,
                NotificationDeliveryRecord.sent_at.is_(None),
                NotificationDeliveryRecord.dead_at.is_(None),
            )
            .values(
                dead_at=func.now(),
                claimed_at=None,
                claim_token=None,
                last_error="DM notifications disabled by the recipient",
            )
        )

    # Recipient selection for each supported event, followed by shared preference checks.

    async def _materialize_staff_submission(self, session: AsyncSession, event: DomainEvent) -> None:
        build = await session.get(Build, event.aggregate_id)
        if build is None or build.submission_status != Status.PENDING:
            return
        staff_account_ids = (
            await session.execute(
                select(Account.id).where(_is_durable_staff_notification_recipient(Account.id)).order_by(Account.id)
            )
        ).scalars()
        payload = {"build_id": build.id, "category": None if build.category is None else str(build.category)}
        await self._insert_candidates(
            session,
            event=event,
            candidates=tuple(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.STAFF_BUILD_SUBMITTED,
                    source_key=NotificationSource(event.id, NotificationKind.STAFF_BUILD_SUBMITTED).key_for(account_id),
                    payload=payload,
                )
                for account_id in staff_account_ids
            ),
        )

    async def _materialize_build_outcome(self, session: AsyncSession, event: DomainEvent) -> None:
        build = await session.get(Build, event.aggregate_id)
        expected_status = Status.CONFIRMED if event.event_type == "build.confirmed" else Status.DENIED
        if (
            build is None
            or build.submission_status != expected_status
            or not await self._is_latest_outcome(session, event)
        ):
            return
        kind = (
            NotificationKind.BUILD_CONFIRMED if event.event_type == "build.confirmed" else NotificationKind.BUILD_DENIED
        )
        assert build.submitter_account_id is not None
        candidates = [
            NotificationCandidate(
                account_id=build.submitter_account_id,
                kind=kind,
                source_key=NotificationSource(event.id, kind).key_for(build.submitter_account_id),
                payload={"build_id": build.id},
            )
        ]
        if event.event_type != "build.confirmed" or not await self._is_first_confirmation(session, event):
            await self._insert_candidates(session, event=event, candidates=candidates)
            return
        # Followers hear about a build's first confirmation, not every later review outcome.
        creator_ids = tuple(await self._creator_public_ids(session, build.id))
        if creator_ids:
            subscriber_ids = (
                await session.execute(
                    select(NotificationSubscription.account_id)
                    .where(
                        NotificationSubscription.kind == SubscriptionKind.CREATOR,
                        NotificationSubscription.subject_id.in_(creator_ids),
                        NotificationSubscription.enabled.is_(True),
                    )
                    .distinct()
                )
            ).scalars()
            candidates.extend(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.CREATOR_BUILD_CONFIRMED,
                    source_key=NotificationSource(event.id, NotificationKind.CREATOR_BUILD_CONFIRMED).key_for(
                        account_id
                    ),
                    payload={"build_id": build.id, "creator_ids": [str(value) for value in creator_ids]},
                )
                for account_id in subscriber_ids
            )
        await self._insert_candidates(session, event=event, candidates=candidates)

    async def _materialize_record_gains(self, session: AsyncSession, event: DomainEvent) -> None:
        """Create one message per recipient and build, combining all newly gained records."""
        # The baseline establishes existing holders; it does not represent newly won records.
        if event.payload.get("baseline") is True:
            return
        previous_run_id = event.payload.get("previous_run_id")
        if isinstance(previous_run_id, bool) or not isinstance(previous_run_id, int):
            return
        gains = await self._record_gains(session, event.aggregate_id, previous_run_id)
        recipients_by_build = await self._record_gain_recipients(session, gains)
        candidates: list[NotificationCandidate] = []
        for build_id, build_gains in gains.items():
            payload: dict[str, object] = {
                "build_id": build_id,
                "records": [
                    {
                        "title_id": str(gain.title_id),
                        "title": gain.title,
                        "base_title": gain.base_title,
                        "build_kind": gain.build_kind,
                        "version_scope": gain.version_scope,
                    }
                    for gain in build_gains
                ],
            }
            candidates.extend(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.RECORD_GAINED,
                    source_key=NotificationSource(event.id, NotificationKind.RECORD_GAINED, build_id).key_for(
                        account_id
                    ),
                    payload=payload,
                )
                for account_id in sorted(recipients_by_build[build_id])
            )
        await self._insert_candidates(session, event=event, candidates=candidates)

    async def _insert_candidates(
        self,
        session: AsyncSession,
        *,
        event: DomainEvent,
        candidates: Sequence[NotificationCandidate],
    ) -> None:
        if not candidates:
            return
        account_ids = {candidate.account_id for candidate in candidates}
        profiles = {
            profile.account_id: profile
            for profile in (
                await session.scalars(
                    select(NotificationProfile)
                    .join(Account, Account.id == NotificationProfile.account_id)
                    .where(
                        NotificationProfile.account_id.in_(account_ids),
                        account_consent_current(),
                        or_(NotificationProfile.web_enabled.is_(True), NotificationProfile.dm_enabled.is_(True)),
                    )
                )
            ).all()
        }
        values = [
            {
                "account_id": candidate.account_id,
                "event_id": event.id,
                "source_key": candidate.source_key,
                "kind": candidate.kind,
                "payload": dict(candidate.payload),
                "web_visible": profiles[candidate.account_id].web_enabled,
            }
            for candidate in sorted(candidates, key=lambda item: (item.account_id, item.source_key))
            if candidate.account_id in profiles
        ]
        if not values:
            return
        # Keep the original visibility snapshot when replaying an existing source key.
        await session.execute(
            insert(InboxNotification)
            .values(values)
            .on_conflict_do_nothing(index_elements=[InboxNotification.source_key])
        )
        source_keys = [value["source_key"] for value in values]
        deliverable = (
            select(InboxNotification.id, InboxNotification.account_id)
            .join(NotificationProfile, NotificationProfile.account_id == InboxNotification.account_id)
            .where(
                InboxNotification.source_key.in_(source_keys),
                NotificationProfile.dm_enabled.is_(True),
                NotificationProfile.dm_suspended_at.is_(None),
                exists().where(
                    AccountIdentity.account_id == InboxNotification.account_id,
                    AccountIdentity.provider == IdentityProvider.DISCORD,
                ),
            )
        )
        await session.execute(
            insert(NotificationDeliveryRecord)
            .from_select(
                [NotificationDeliveryRecord.notification_id, NotificationDeliveryRecord.account_id],
                deliverable,
            )
            .on_conflict_do_nothing(index_elements=[NotificationDeliveryRecord.notification_id])
        )

    @staticmethod
    async def _is_first_confirmation(session: AsyncSession, event: DomainEvent) -> bool:
        previous = await session.scalar(
            select(
                exists().where(
                    DomainEvent.aggregate_kind == "build",
                    DomainEvent.aggregate_id == event.aggregate_id,
                    DomainEvent.event_type == "build.confirmed",
                    DomainEvent.id < event.id,
                )
            )
        )
        return not previous

    @staticmethod
    async def _is_latest_outcome(session: AsyncSession, event: DomainEvent) -> bool:
        latest_id = await session.scalar(
            select(func.max(DomainEvent.id)).where(
                DomainEvent.aggregate_kind == "build",
                DomainEvent.aggregate_id == event.aggregate_id,
                DomainEvent.event_type.in_(("build.confirmed", "build.denied")),
            )
        )
        return latest_id == event.id

    @staticmethod
    async def _creator_public_ids(session: AsyncSession, build_id: int) -> Sequence[UUID]:
        return tuple(
            (
                await session.execute(
                    select(Account.public_creator_id)
                    .join(CreatorAlias, CreatorAlias.account_id == Account.id)
                    .join(BuildCreator, BuildCreator.alias_id == CreatorAlias.id)
                    .where(BuildCreator.build_id == build_id)
                    .distinct()
                )
            ).scalars()
        )

    @staticmethod
    async def _record_gains(
        session: AsyncSession, run_id: int, previous_run_id: int
    ) -> dict[int, tuple[_RecordGain, ...]]:
        new_rows = (
            await session.execute(
                select(
                    TitleDefinition.title_id,
                    TitleHolder.build_id,
                    TitleDefinition.title,
                    RecordTitle.base_title,
                    RecordTitle.build_kind,
                    RecordTitle.version_scope,
                )
                .select_from(TitleDefinition)
                .join(RecordTitle, RecordTitle.public_id == TitleDefinition.title_id)
                .join(TitleResult, TitleResult.definition_id == TitleDefinition.id)
                .join(TitleHolder, TitleHolder.result_id == TitleResult.id)
                .where(TitleResult.run_id == run_id)
            )
        ).all()
        old_holders = set(
            await session.execute(
                select(TitleDefinition.title_id, TitleHolder.build_id)
                .select_from(TitleDefinition)
                .join(RecordTitle, RecordTitle.public_id == TitleDefinition.title_id)
                .join(TitleResult, TitleResult.definition_id == TitleDefinition.id)
                .join(TitleHolder, TitleHolder.result_id == TitleResult.id)
                .where(TitleResult.run_id == previous_run_id)
            )
        )
        grouped: dict[int, list[_RecordGain]] = defaultdict(list)
        for title_id, build_id, title, base_title, build_kind, version_scope in new_rows:
            if (title_id, build_id) in old_holders:
                continue
            grouped[build_id].append(_RecordGain(title_id, build_id, title, base_title, build_kind, version_scope))
        return {build_id: tuple(values) for build_id, values in grouped.items()}

    @staticmethod
    async def _record_gain_recipients(
        session: AsyncSession,
        gains: dict[int, tuple[_RecordGain, ...]],
    ) -> dict[int, set[int]]:
        """Collect creators and matching subscribers once per build.

        Matching any gained record qualifies a subscriber. The resulting message
        includes all of that build's gains, even when only one matched the filter.
        """
        build_ids = set(gains)
        recipients = {build_id: set() for build_id in build_ids}
        if not build_ids:
            return recipients
        creator_rows = (
            await session.execute(
                select(BuildCreator.build_id, Account.id, Account.public_creator_id)
                .join(CreatorAlias, BuildCreator.alias_id == CreatorAlias.id)
                .join(Account, Account.id == CreatorAlias.account_id)
                .where(BuildCreator.build_id.in_(build_ids))
            )
        ).all()
        creator_ids: dict[int, set[UUID]] = defaultdict(set)
        for build_id, account_id, public_creator_id in creator_rows:
            recipients[build_id].add(account_id)
            creator_ids[build_id].add(public_creator_id)

        relevant_subjects: set[UUID] = set().union(*creator_ids.values())
        relevant_subjects.update(gain.title_id for build_gains in gains.values() for gain in build_gains)
        exact_subscriptions = (
            await session.execute(
                select(
                    NotificationSubscription.account_id,
                    NotificationSubscription.kind,
                    NotificationSubscription.subject_id,
                ).where(
                    NotificationSubscription.kind.in_((SubscriptionKind.CREATOR, SubscriptionKind.RECORD)),
                    NotificationSubscription.subject_id.in_(relevant_subjects),
                    NotificationSubscription.enabled.is_(True),
                )
            )
        ).all()
        for account_id, kind, subject_id in exact_subscriptions:
            for build_id, build_gains in gains.items():
                subjects = (
                    creator_ids[build_id]
                    if kind is SubscriptionKind.CREATOR
                    else {gain.title_id for gain in build_gains}
                )
                if subject_id in subjects:
                    recipients[build_id].add(account_id)

        filter_rows = (
            await session.execute(
                select(NotificationSubscription.account_id, NotificationSubscription.record_filter).where(
                    NotificationSubscription.kind == SubscriptionKind.RECORD_FILTER,
                    NotificationSubscription.enabled.is_(True),
                )
            )
        ).all()
        assignments = (
            await session.execute(
                select(
                    TagAssignment.build_id,
                    TagAssignment.tag_id,
                    TagAssignment.numeric_value,
                    TagAssignment.text_value,
                    TagAssignment.boolean_value,
                ).where(TagAssignment.build_id.in_(build_ids))
            )
        ).all()
        tags_by_build: dict[int, dict[int, Decimal | str | bool | None]] = defaultdict(dict)
        for build_id, tag_id, numeric, text_value, boolean in assignments:
            tags_by_build[build_id][tag_id] = (
                numeric if numeric is not None else text_value if text_value is not None else boolean
            )
        for account_id, raw_filter in filter_rows:
            if raw_filter is None:
                continue
            record_filter = raw_filter
            for build_id, build_gains in gains.items():
                if any(_filter_matches(record_filter, gain, tags_by_build[build_id]) for gain in build_gains):
                    recipients[build_id].add(account_id)
        return recipients


def _preferences(
    account_id: int, profile: NotificationProfile | None, consent_pending: bool
) -> NotificationPreferences:
    if profile is None:
        return NotificationPreferences(account_id=account_id, consent_pending=consent_pending)
    return NotificationPreferences(
        account_id=account_id,
        revision=profile.revision,
        consent_pending=consent_pending,
        web_enabled=profile.web_enabled,
        dm_enabled=profile.dm_enabled,
        dm_suspended_at=profile.dm_suspended_at,
    )


def _inbox_predicates(account_id: int, visibility: InboxVisibility) -> tuple[ColumnElement[bool], ...]:
    predicates = (
        InboxNotification.account_id == account_id,
        InboxNotification.web_visible.is_(True),
        exists().where(
            NotificationProfile.account_id == account_id,
            NotificationProfile.web_enabled.is_(True),
            Account.id == NotificationProfile.account_id,
            account_consent_current(),
        ),
    )
    if visibility.include_staff:
        return predicates
    return (*predicates, InboxNotification.kind != NotificationKind.STAFF_BUILD_SUBMITTED)


def _inbox_filter[S: Select[Any]](statement: S, *, account_id: int, visibility: InboxVisibility) -> S:
    """Apply the visibility rules shared by inbox reads and state transitions."""
    return statement.where(*_inbox_predicates(account_id, visibility))


def _filter_matches(
    record_filter: RecordSubscriptionFilter,
    gain: _RecordGain,
    tags: dict[int, Decimal | str | bool | None],
) -> bool:
    if record_filter.build_kinds and gain.build_kind not in record_filter.build_kinds:
        return False
    if record_filter.base_titles and gain.base_title not in record_filter.base_titles:
        return False
    if record_filter.version_scopes and gain.version_scope not in record_filter.version_scopes:
        return False
    for predicate in record_filter.tags:
        if predicate.tag_id not in tags:
            return False
        if predicate.operator == "exact" and not _exact_value(tags[predicate.tag_id], predicate.value):
            return False
    return True


def _exact_value(stored: Decimal | str | bool | None, expected: str | int | float | bool | None) -> bool:
    if stored is None or expected is None:
        return False
    if isinstance(stored, bool):
        return isinstance(expected, bool) and stored is expected
    if isinstance(stored, Decimal):
        if isinstance(expected, bool):
            return False
        try:
            return stored == Decimal(str(expected))
        except ArithmeticError:
            return False
    return isinstance(expected, str) and stored == expected


def _claim_token(delivery: NotificationDeliveryRecord) -> UUID:
    if delivery.claim_token is None:
        msg = "claimed notification delivery has no fencing token"
        raise RuntimeError(msg)
    return delivery.claim_token


def _delivery_claim(delivery: PendingNotificationDelivery) -> tuple[ColumnElement[bool], ...]:
    return (
        NotificationDeliveryRecord.id == delivery.id,
        NotificationDeliveryRecord.generation == delivery.generation,
        NotificationDeliveryRecord.claim_token == delivery.claim_token,
    )
