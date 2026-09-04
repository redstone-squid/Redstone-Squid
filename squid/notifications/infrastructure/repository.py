"""PostgreSQL notification persistence and domain-event materialization."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.consent import account_consent_current
from squid.accounts.infrastructure.models import Account, AccountIdentity, CreatorAlias
from squid.builds.domain import Status
from squid.builds.infrastructure.models import Build, BuildCreator
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventDeliveryRecord, DomainEventRecord
from squid.notifications.domain import (
    InboxNotification,
    InboxVisibility,
    NotificationCandidate,
    NotificationKind,
    NotificationPreferences,
    NotificationSubscription,
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
from squid.permissions.domain import BuiltinRoleKeys
from squid.permissions.infrastructure.models import PermissionRole, PermissionRoleAssignment
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.records.infrastructure.models import (
    RecordResultHolder,
    RecordRule,
    RecordSeries,
    RecordStanding,
)
from squid.tags.infrastructure.models import BuildTagAssignment


def _durable_staff_notification_role_ids():
    """The durable staff-notification audience role's id, as a scalar subquery.

    Background delivery has no request, guild, Discord-role membership, or configured-owner
    context to resolve. Its deliberately narrower audience is therefore an active, global,
    direct account assignment to the built-in `global-admin` role.

    This answers only *whom do we notify*. Per-request staff inbox visibility separately
    resolves `build.submission.view_pending`; holding that node through owner or custom-role
    context does not implicitly subscribe an account to unsolicited staff notifications.
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


@dataclass(frozen=True, slots=True)
class _RecordGain:
    competition_id: UUID
    build_id: int
    title: str
    record_class: str
    build_kind: str
    version_scope: str


class PostgresNotificationRepository:
    """Persist opt-ins and project durable events into channel-specific work."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        async with self._session_factory() as session:
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

        An upsert rather than an update because there is no longer a separate accept step to
        have created the row: a consented account turning a channel on for the first time is one
        call. The gate is read first and separately because an UPDATE cannot join to `accounts`,
        and this runs when a human toggles a switch, so a second round trip costs nothing.
        """
        async with self._session_factory() as session, session.begin():
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
                        },
                    )
                    .returning(NotificationProfile)
                )
            ).one()
            if not dm_enabled:
                await self._cancel_pending_deliveries(session, account_id)
            return _preferences(account_id, profile, consent_pending=False)

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool:
        async with self._session_factory() as session:
            if kind is SubscriptionKind.CREATOR:
                statement = select(
                    exists().where(
                        Account.public_creator_id == subject_id,
                        CreatorAlias.account_id == Account.id,
                    )
                )
            else:
                statement = select(exists().where(RecordSeries.public_id == subject_id))
            return bool(await session.scalar(statement))

    async def add_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription:
        filter_value = None if record_filter is None else record_filter.as_dict()
        async with self._session_factory() as session, session.begin():
            predicates = [
                NotificationSubscriptionRecord.account_id == account_id,
                NotificationSubscriptionRecord.kind == kind,
                NotificationSubscriptionRecord.enabled.is_(True),
            ]
            if subject_id is None:
                predicates.extend(
                    [
                        NotificationSubscriptionRecord.subject_id.is_(None),
                        NotificationSubscriptionRecord.filter == filter_value,
                    ]
                )
            else:
                predicates.append(NotificationSubscriptionRecord.subject_id == subject_id)
            existing = await session.scalar(select(NotificationSubscriptionRecord).where(*predicates))
            if existing is not None:
                return _subscription(existing)
            row = await session.scalar(
                insert(NotificationSubscriptionRecord)
                .values(
                    account_id=account_id,
                    kind=kind,
                    subject_id=subject_id,
                    filter=filter_value,
                )
                .on_conflict_do_nothing()
                .returning(NotificationSubscriptionRecord)
            )
            if row is None:
                row = await session.scalar(select(NotificationSubscriptionRecord).where(*predicates))
            if row is None:
                msg = "notification subscription conflict did not resolve to an equivalent row"
                raise RuntimeError(msg)
            return _subscription(row)

    async def list_subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(NotificationSubscriptionRecord)
                    .where(
                        NotificationSubscriptionRecord.account_id == account_id,
                        NotificationSubscriptionRecord.enabled.is_(True),
                    )
                    .order_by(NotificationSubscriptionRecord.created_at, NotificationSubscriptionRecord.id)
                )
            ).all()
            return tuple(_subscription(row) for row in rows)

    async def delete_subscription(self, account_id: int, subscription_id: int) -> bool:
        async with self._session_factory() as session, session.begin():
            removed = await session.scalar(
                delete(NotificationSubscriptionRecord)
                .where(
                    NotificationSubscriptionRecord.id == subscription_id,
                    NotificationSubscriptionRecord.account_id == account_id,
                )
                .returning(NotificationSubscriptionRecord.id)
            )
            return removed is not None

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
        async with self._session_factory() as session:
            statement = _inbox_filter(select(NotificationRecord), account_id=account_id, visibility=visibility)
            reverse = before_id is not None
            if before_id is not None:
                # Walk away from the anchor in reversed display order; the page is flipped back below.
                statement = statement.where(NotificationRecord.id > before_id).order_by(NotificationRecord.id.asc())
            else:
                if after_id is not None:
                    statement = statement.where(NotificationRecord.id < after_id)
                statement = statement.order_by(NotificationRecord.id.desc())
            if offset:
                statement = statement.offset(offset)
            rows = (await session.scalars(statement.limit(limit))).all()
            ordered = reversed(rows) if reverse else rows
            return tuple(_inbox(row) for row in ordered)

    async def count_inbox(self, account_id: int, *, visibility: InboxVisibility) -> int:
        """Count web-visible inbox items, mirroring the visibility rules of `list_inbox`."""
        async with self._session_factory() as session:
            statement = _inbox_filter(
                select(func.count()).select_from(NotificationRecord),
                account_id=account_id,
                visibility=visibility,
            )
            return await session.scalar(statement) or 0

    async def mark_read(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        return await self._set_read_state(account_id, notification_id, visibility=visibility, read=True)

    async def mark_unread(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        return await self._set_read_state(account_id, notification_id, visibility=visibility, read=False)

    async def _set_read_state(
        self,
        account_id: int,
        notification_id: int,
        *,
        visibility: InboxVisibility,
        read: bool,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            updated = await session.scalar(
                update(NotificationRecord)
                .where(NotificationRecord.id == notification_id, *_inbox_predicates(account_id, visibility))
                .values(read_at=func.coalesce(NotificationRecord.read_at, func.now()) if read else None)
                .returning(NotificationRecord.id)
            )
            return updated is not None

    async def materialize(self, event: DomainEvent) -> None:
        async with self._session_factory() as session, session.begin():
            if event.event_type == "build.submitted":
                await self._materialize_staff_submission(session, event)
            elif event.event_type in {"build.confirmed", "build.denied"}:
                await self._materialize_build_outcome(session, event)
            elif event.event_type == "record_run.activated":
                await self._materialize_record_gains(session, event)

    async def cleanup(self, *, retention_days: int) -> int:
        async with self._session_factory() as session, session.begin():
            cutoff = func.now() - func.make_interval(0, 0, 0, retention_days)
            removed_rows = (
                delete(NotificationRecord)
                .where(NotificationRecord.created_at < cutoff)
                .returning(NotificationRecord.id)
                .cte("removed_notifications")
            )
            removed = await session.scalar(select(func.count()).select_from(removed_rows))
            await session.execute(
                delete(DomainEventRecord).where(
                    DomainEventRecord.occurred_at < cutoff,
                    ~exists().where(NotificationRecord.event_id == DomainEventRecord.id),
                    ~exists().where(DomainEventDeliveryRecord.event_id == DomainEventRecord.id),
                )
            )
            return int(removed or 0)

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]:
        async with self._session_factory() as session, session.begin():
            claimable_staff = _is_durable_staff_notification_recipient(NotificationDeliveryRecord.account_id)
            # The DM address is read at claim time rather than copied onto the delivery
            # row at enqueue time. The write path already made this join to decide
            # whether to enqueue at all, so the cost moves rather than appearing -- and
            # the inner join means unlinking Discord suppresses a pending DM, which is
            # the correct reading of an unlink.
            candidates = tuple(
                (
                    await session.execute(
                        select(NotificationDeliveryRecord.id, AccountIdentity.subject)
                        .join(
                            NotificationProfile,
                            NotificationProfile.account_id == NotificationDeliveryRecord.account_id,
                        )
                        .join(NotificationRecord, NotificationRecord.id == NotificationDeliveryRecord.notification_id)
                        .join(Account, Account.id == NotificationDeliveryRecord.account_id)
                        .join(
                            AccountIdentity,
                            (AccountIdentity.account_id == NotificationDeliveryRecord.account_id)
                            & (AccountIdentity.provider == IdentityProvider.DISCORD),
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
                            or_(
                                NotificationRecord.kind != NotificationKind.STAFF_BUILD_SUBMITTED,
                                claimable_staff,
                            ),
                        )
                        .order_by(NotificationDeliveryRecord.available_at, NotificationDeliveryRecord.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True, of=NotificationDeliveryRecord)
                    )
                ).all()
            )
            if not candidates:
                return ()
            discord_by_delivery = {delivery_id: int(subject) for delivery_id, subject in candidates}
            ids = tuple(discord_by_delivery)
            claimed = (
                await session.execute(
                    update(NotificationDeliveryRecord)
                    .where(NotificationDeliveryRecord.id.in_(ids))
                    .values(
                        claimed_at=func.now(),
                        claim_token=func.gen_random_uuid(),
                        attempts=NotificationDeliveryRecord.attempts + 1,
                    )
                    .returning(NotificationDeliveryRecord)
                )
            ).scalars()
            deliveries = tuple(claimed)
            notification_by_id = {
                row.id: row
                for row in (
                    await session.scalars(
                        select(NotificationRecord).where(
                            NotificationRecord.id.in_([delivery.notification_id for delivery in deliveries])
                        )
                    )
                ).all()
            }
            return tuple(
                PendingNotificationDelivery(
                    id=delivery.id,
                    generation=delivery.generation,
                    discord_id=discord_by_delivery[delivery.id],
                    nonce=delivery.nonce,
                    claim_token=_claim_token(delivery),
                    attempts=delivery.attempts,
                    kind=notification_by_id[delivery.notification_id].kind,
                    payload=dict(notification_by_id[delivery.notification_id].payload),
                )
                for delivery in deliveries
            )

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        async with self._session_factory() as session, session.begin():
            updated = await session.scalar(
                update(NotificationDeliveryRecord)
                .where(*_delivery_claim(delivery))
                .values(sent_at=func.now(), claimed_at=None, claim_token=None, last_error=None)
                .returning(NotificationDeliveryRecord.id)
            )
            return updated is not None

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool:
        async with self._session_factory() as session, session.begin():
            if delivery.attempts >= max_attempts:
                updated = await session.scalar(
                    update(NotificationDeliveryRecord)
                    .where(*_delivery_claim(delivery))
                    .values(
                        dead_at=func.now(),
                        claimed_at=None,
                        claim_token=None,
                        last_error=error[:4000],
                    )
                    .returning(NotificationDeliveryRecord.id)
                )
                return updated is not None
            await session.scalar(
                update(NotificationDeliveryRecord)
                .where(*_delivery_claim(delivery))
                .values(
                    available_at=func.now() + retry_delay(delivery.attempts),
                    claimed_at=None,
                    claim_token=None,
                    last_error=error[:4000],
                )
                .returning(NotificationDeliveryRecord.id)
            )
            return False

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool:
        async with self._session_factory() as session, session.begin():
            account_id = await session.scalar(
                update(NotificationDeliveryRecord)
                .where(*_delivery_claim(delivery))
                .values(
                    dead_at=func.now(),
                    claimed_at=None,
                    claim_token=None,
                    last_error=error[:4000],
                )
                .returning(NotificationDeliveryRecord.account_id)
            )
            if account_id is None:
                return False
            await session.execute(
                update(NotificationProfile)
                .where(NotificationProfile.account_id == account_id)
                .values(dm_enabled=False, dm_suspended_at=func.now(), updated_at=func.now())
            )
            return True

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
                    source_key=f"event:{event.id}:staff:{account_id}",
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
        candidates = [
            NotificationCandidate(
                account_id=build.submitter_account_id,
                kind=kind,
                source_key=f"event:{event.id}:owner:{build.submitter_account_id}",
                payload={"build_id": build.id},
            )
        ]
        if event.event_type != "build.confirmed" or not await self._is_first_confirmation(session, event):
            await self._insert_candidates(session, event=event, candidates=candidates)
            return
        creator_ids = tuple(await self._creator_public_ids(session, build.id))
        if creator_ids:
            subscriber_ids = (
                await session.execute(
                    select(NotificationSubscriptionRecord.account_id)
                    .where(
                        NotificationSubscriptionRecord.kind == SubscriptionKind.CREATOR,
                        NotificationSubscriptionRecord.subject_id.in_(creator_ids),
                        NotificationSubscriptionRecord.enabled.is_(True),
                    )
                    .distinct()
                )
            ).scalars()
            candidates.extend(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.CREATOR_BUILD_CONFIRMED,
                    source_key=f"event:{event.id}:creator:{account_id}",
                    payload={"build_id": build.id, "creator_ids": [str(value) for value in creator_ids]},
                )
                for account_id in subscriber_ids
            )
        await self._insert_candidates(session, event=event, candidates=candidates)

    async def _materialize_record_gains(self, session: AsyncSession, event: DomainEvent) -> None:
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
                        "competition_id": str(gain.competition_id),
                        "title": gain.title,
                        "record_class": gain.record_class,
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
                    source_key=f"event:{event.id}:record-build:{build_id}:account:{account_id}",
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
        await session.execute(
            insert(NotificationRecord)
            .values(values)
            .on_conflict_do_nothing(index_elements=[NotificationRecord.source_key])
        )
        source_keys = [value["source_key"] for value in values]
        deliverable = (
            select(NotificationRecord.id, NotificationRecord.account_id)
            .join(NotificationProfile, NotificationProfile.account_id == NotificationRecord.account_id)
            .where(
                NotificationRecord.source_key.in_(source_keys),
                NotificationProfile.dm_enabled.is_(True),
                NotificationProfile.dm_suspended_at.is_(None),
                exists().where(
                    AccountIdentity.account_id == NotificationRecord.account_id,
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
                    DomainEventRecord.aggregate_kind == "build",
                    DomainEventRecord.aggregate_id == event.aggregate_id,
                    DomainEventRecord.event_type == "build.confirmed",
                    DomainEventRecord.id < event.id,
                )
            )
        )
        return not previous

    @staticmethod
    async def _is_latest_outcome(session: AsyncSession, event: DomainEvent) -> bool:
        latest_id = await session.scalar(
            select(func.max(DomainEventRecord.id)).where(
                DomainEventRecord.aggregate_kind == "build",
                DomainEventRecord.aggregate_id == event.aggregate_id,
                DomainEventRecord.event_type.in_(("build.confirmed", "build.denied")),
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
                    RecordRule.competition_id,
                    RecordResultHolder.build_id,
                    RecordRule.title,
                    RecordRule.record_class,
                    RecordRule.build_kind,
                    RecordRule.version_scope,
                )
                .join(RecordStanding, RecordStanding.definition_id == RecordRule.id)
                .join(RecordResultHolder, RecordResultHolder.result_id == RecordStanding.id)
                .where(RecordStanding.run_id == run_id)
            )
        ).all()
        old_holders = set(
            (
                await session.execute(
                    select(RecordRule.competition_id, RecordResultHolder.build_id)
                    .join(RecordStanding, RecordStanding.definition_id == RecordRule.id)
                    .join(RecordResultHolder, RecordResultHolder.result_id == RecordStanding.id)
                    .where(RecordStanding.run_id == previous_run_id)
                )
            ).tuples()
        )
        grouped: dict[int, list[_RecordGain]] = defaultdict(list)
        for competition_id, build_id, title, record_class, build_kind, version_scope in new_rows:
            if (competition_id, build_id) in old_holders:
                continue
            grouped[build_id].append(
                _RecordGain(competition_id, build_id, title, record_class, build_kind, version_scope)
            )
        return {build_id: tuple(values) for build_id, values in grouped.items()}

    @staticmethod
    async def _record_gain_recipients(
        session: AsyncSession,
        gains: dict[int, tuple[_RecordGain, ...]],
    ) -> dict[int, set[int]]:
        build_ids = set(gains)
        recipients = {build_id: set() for build_id in build_ids}
        if not build_ids:
            return recipients
        creator_rows = (
            await session.execute(
                select(BuildCreator.build_id, CreatorAlias.account_id, Account.public_creator_id)
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
        relevant_subjects.update(gain.competition_id for build_gains in gains.values() for gain in build_gains)
        exact_subscriptions = (
            await session.execute(
                select(
                    NotificationSubscriptionRecord.account_id,
                    NotificationSubscriptionRecord.kind,
                    NotificationSubscriptionRecord.subject_id,
                ).where(
                    NotificationSubscriptionRecord.kind.in_((SubscriptionKind.CREATOR, SubscriptionKind.RECORD)),
                    NotificationSubscriptionRecord.subject_id.in_(relevant_subjects),
                    NotificationSubscriptionRecord.enabled.is_(True),
                )
            )
        ).all()
        for account_id, kind, subject_id in exact_subscriptions:
            for build_id, build_gains in gains.items():
                subjects = (
                    creator_ids[build_id]
                    if kind is SubscriptionKind.CREATOR
                    else {gain.competition_id for gain in build_gains}
                )
                if subject_id in subjects:
                    recipients[build_id].add(account_id)

        filter_rows = (
            await session.execute(
                select(NotificationSubscriptionRecord.account_id, NotificationSubscriptionRecord.filter).where(
                    NotificationSubscriptionRecord.kind == SubscriptionKind.RECORD_FILTER,
                    NotificationSubscriptionRecord.enabled.is_(True),
                )
            )
        ).all()
        assignments = (
            await session.execute(
                select(
                    BuildTagAssignment.build_id,
                    BuildTagAssignment.tag_id,
                    BuildTagAssignment.numeric_value,
                    BuildTagAssignment.text_value,
                    BuildTagAssignment.boolean_value,
                ).where(BuildTagAssignment.build_id.in_(build_ids))
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
            record_filter = RecordSubscriptionFilter.from_dict(dict(raw_filter))
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
        consent_pending=consent_pending,
        web_enabled=profile.web_enabled,
        dm_enabled=profile.dm_enabled,
        dm_suspended_at=profile.dm_suspended_at,
    )


def _subscription(row: NotificationSubscriptionRecord) -> NotificationSubscription:
    return NotificationSubscription(
        id=row.id,
        account_id=row.account_id,
        kind=row.kind,
        subject_id=row.subject_id,
        record_filter=None if row.filter is None else RecordSubscriptionFilter.from_dict(dict(row.filter)),
        created_at=row.created_at,
    )


def _inbox_predicates(account_id: int, visibility: InboxVisibility) -> tuple[ColumnElement[bool], ...]:
    predicates = (
        NotificationRecord.account_id == account_id,
        NotificationRecord.web_visible.is_(True),
        exists().where(
            NotificationProfile.account_id == account_id,
            NotificationProfile.web_enabled.is_(True),
            Account.id == NotificationProfile.account_id,
            account_consent_current(),
        ),
    )
    if visibility.include_staff:
        return predicates
    return (*predicates, NotificationRecord.kind != NotificationKind.STAFF_BUILD_SUBMITTED)


def _inbox_filter[S: Select[Any]](statement: S, *, account_id: int, visibility: InboxVisibility) -> S:
    """Apply the visibility rules shared by inbox reads and state transitions."""
    return statement.where(*_inbox_predicates(account_id, visibility))


def _inbox(row: NotificationRecord) -> InboxNotification:
    return InboxNotification(
        id=row.id,
        kind=row.kind,
        payload=dict(row.payload),
        created_at=row.created_at,
        read_at=row.read_at,
    )


def _filter_matches(
    record_filter: RecordSubscriptionFilter,
    gain: _RecordGain,
    tags: dict[int, Decimal | str | bool | None],
) -> bool:
    if record_filter.build_kinds and gain.build_kind not in record_filter.build_kinds:
        return False
    if record_filter.record_classes and gain.record_class not in record_filter.record_classes:
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
