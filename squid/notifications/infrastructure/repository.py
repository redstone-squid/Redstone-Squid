"""PostgreSQL notification persistence and domain-event materialization."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity, CreatorAlias
from squid.builds.domain import Status
from squid.builds.infrastructure.models import Build, BuildCreator
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventDeliveryRecord, DomainEventRecord
from squid.notifications.domain import (
    CURRENT_NOTIFICATION_NOTICE_VERSION,
    InboxNotification,
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
from squid.permissions.infrastructure.models import GlobalAdministrator
from squid.records.infrastructure.models import (
    RecordCompetition,
    RecordDefinition,
    RecordResult,
    RecordResultHolder,
)
from squid.tags.infrastructure.models import BuildTagAssignment


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

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        staff_discord_ids: Sequence[int] = (),
    ) -> None:
        self._session_factory = session_factory
        self._staff_discord_ids = tuple(staff_discord_ids)

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        async with self._session_factory() as session:
            profile = await session.get(NotificationProfile, account_id)
            return _preferences(account_id, profile)

    async def accept_notice(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        async with self._session_factory() as session, session.begin():
            statement = (
                insert(NotificationProfile)
                .values(
                    account_id=account_id,
                    notice_version=CURRENT_NOTIFICATION_NOTICE_VERSION,
                    consented_at=func.now(),
                    web_enabled=web_enabled,
                    dm_enabled=dm_enabled,
                    dm_suspended_at=None,
                    updated_at=func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[NotificationProfile.account_id],
                    set_={
                        "notice_version": CURRENT_NOTIFICATION_NOTICE_VERSION,
                        "consented_at": func.now(),
                        "web_enabled": web_enabled,
                        "dm_enabled": dm_enabled,
                        "dm_suspended_at": None,
                        "updated_at": func.now(),
                    },
                )
                .returning(NotificationProfile)
            )
            profile = (await session.scalars(statement)).one()
            if not dm_enabled:
                await self._cancel_pending_deliveries(session, account_id)
            return _preferences(account_id, profile)

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None:
        async with self._session_factory() as session, session.begin():
            profile = await session.scalar(
                update(NotificationProfile)
                .where(
                    NotificationProfile.account_id == account_id,
                    NotificationProfile.notice_version == CURRENT_NOTIFICATION_NOTICE_VERSION,
                    NotificationProfile.consented_at.is_not(None),
                )
                .values(
                    web_enabled=web_enabled,
                    dm_enabled=dm_enabled,
                    dm_suspended_at=None if dm_enabled else NotificationProfile.dm_suspended_at,
                    updated_at=func.now(),
                )
                .returning(NotificationProfile)
            )
            if profile is not None and not dm_enabled:
                await self._cancel_pending_deliveries(session, account_id)
            return None if profile is None else _preferences(account_id, profile)

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
                statement = select(exists().where(RecordCompetition.public_id == subject_id))
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
                NotificationSubscriptionRecord.kind == kind.value,
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
                    kind=kind.value,
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
        self, account_id: int, *, after_id: int | None, limit: int, include_staff: bool
    ) -> Sequence[InboxNotification]:
        async with self._session_factory() as session:
            enabled = await session.scalar(
                select(NotificationProfile.web_enabled).where(
                    NotificationProfile.account_id == account_id,
                    NotificationProfile.notice_version == CURRENT_NOTIFICATION_NOTICE_VERSION,
                )
            )
            if not enabled:
                return ()
            statement = select(NotificationRecord).where(
                NotificationRecord.account_id == account_id,
                NotificationRecord.web_visible.is_(True),
            )
            if after_id is not None:
                statement = statement.where(NotificationRecord.id < after_id)
            if not include_staff:
                statement = statement.where(NotificationRecord.kind != NotificationKind.STAFF_BUILD_SUBMITTED.value)
            rows = (await session.scalars(statement.order_by(NotificationRecord.id.desc()).limit(limit))).all()
            return tuple(_inbox(row) for row in rows)

    async def mark_read(self, account_id: int, notification_id: int, *, include_staff: bool) -> bool:
        async with self._session_factory() as session, session.begin():
            predicates = [
                NotificationRecord.id == notification_id,
                NotificationRecord.account_id == account_id,
                NotificationRecord.web_visible.is_(True),
            ]
            if not include_staff:
                predicates.append(NotificationRecord.kind != NotificationKind.STAFF_BUILD_SUBMITTED.value)
            updated = await session.scalar(
                update(NotificationRecord)
                .where(*predicates)
                .values(read_at=func.coalesce(NotificationRecord.read_at, func.now()))
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

    async def can_view_staff(self, discord_id: int) -> bool:
        if discord_id in self._staff_discord_ids:
            return True
        async with self._session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists()
                        .where(GlobalAdministrator.account_id == AccountIdentity.account_id)
                        .where(
                            AccountIdentity.provider == IdentityProvider.DISCORD,
                            AccountIdentity.subject == str(discord_id),
                        )
                    )
                )
            )

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]:
        async with self._session_factory() as session, session.begin():
            claimable_staff = exists().where(
                GlobalAdministrator.account_id == NotificationDeliveryRecord.account_id
            ) | NotificationDeliveryRecord.discord_id.in_(self._staff_discord_ids)
            ids = tuple(
                (
                    await session.execute(
                        select(NotificationDeliveryRecord.id)
                        .join(
                            NotificationProfile,
                            NotificationProfile.account_id == NotificationDeliveryRecord.account_id,
                        )
                        .join(NotificationRecord, NotificationRecord.id == NotificationDeliveryRecord.notification_id)
                        .where(
                            NotificationDeliveryRecord.available_at <= func.now(),
                            NotificationDeliveryRecord.dead_at.is_(None),
                            NotificationDeliveryRecord.sent_at.is_(None),
                            or_(
                                NotificationDeliveryRecord.claimed_at.is_(None),
                                NotificationDeliveryRecord.claimed_at < func.now() - _visibility_timeout(),
                            ),
                            NotificationProfile.notice_version == CURRENT_NOTIFICATION_NOTICE_VERSION,
                            NotificationProfile.dm_enabled.is_(True),
                            NotificationProfile.dm_suspended_at.is_(None),
                            or_(
                                NotificationRecord.kind != NotificationKind.STAFF_BUILD_SUBMITTED.value,
                                claimable_staff,
                            ),
                        )
                        .order_by(NotificationDeliveryRecord.available_at, NotificationDeliveryRecord.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True, of=NotificationDeliveryRecord)
                    )
                ).scalars()
            )
            if not ids:
                return ()
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
                    discord_id=delivery.discord_id,
                    nonce=delivery.nonce,
                    claim_token=_claim_token(delivery),
                    attempts=delivery.attempts,
                    kind=NotificationKind(notification_by_id[delivery.notification_id].kind),
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
                    available_at=func.now() + _retry_delay(delivery.attempts),
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
                select(Account.id)
                .outerjoin(GlobalAdministrator, GlobalAdministrator.account_id == Account.id)
                .outerjoin(
                    AccountIdentity,
                    (AccountIdentity.account_id == Account.id) & (AccountIdentity.provider == IdentityProvider.DISCORD),
                )
                .where(
                    or_(
                        GlobalAdministrator.account_id.is_not(None),
                        AccountIdentity.subject.in_(str(value) for value in self._staff_discord_ids),
                    )
                )
                .order_by(Account.id)
            )
        ).scalars()
        for account_id in staff_account_ids:
            await self._insert(
                session,
                event=event,
                account_id=account_id,
                kind=NotificationKind.STAFF_BUILD_SUBMITTED,
                source_key=f"event:{event.id}:staff:{account_id}",
                payload={"build_id": build.id, "category": None if build.category is None else str(build.category)},
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
        await self._insert(
            session,
            event=event,
            account_id=build.submitter_account_id,
            kind=kind,
            source_key=f"event:{event.id}:owner:{build.submitter_account_id}",
            payload={"build_id": build.id},
        )
        if event.event_type != "build.confirmed" or not await self._is_first_confirmation(session, event):
            return
        creator_ids = tuple(await self._creator_public_ids(session, build.id))
        if not creator_ids:
            return
        subscriber_ids = (
            await session.execute(
                select(NotificationSubscriptionRecord.account_id)
                .where(
                    NotificationSubscriptionRecord.kind == SubscriptionKind.CREATOR.value,
                    NotificationSubscriptionRecord.subject_id.in_(creator_ids),
                    NotificationSubscriptionRecord.enabled.is_(True),
                )
                .distinct()
            )
        ).scalars()
        for account_id in subscriber_ids:
            await self._insert(
                session,
                event=event,
                account_id=account_id,
                kind=NotificationKind.CREATOR_BUILD_CONFIRMED,
                source_key=f"event:{event.id}:creator:{account_id}",
                payload={"build_id": build.id, "creator_ids": [str(value) for value in creator_ids]},
            )

    async def _materialize_record_gains(self, session: AsyncSession, event: DomainEvent) -> None:
        if event.payload.get("baseline") is True:
            return
        previous_run_id = event.payload.get("previous_run_id")
        if isinstance(previous_run_id, bool) or not isinstance(previous_run_id, int):
            return
        gains = await self._record_gains(session, event.aggregate_id, previous_run_id)
        for build_id, build_gains in gains.items():
            recipients = set(await self._creator_account_ids(session, build_id))
            creator_ids = tuple(await self._creator_public_ids(session, build_id))
            competition_ids = {gain.competition_id for gain in build_gains}
            subscribed = (
                await session.execute(
                    select(NotificationSubscriptionRecord.account_id).where(
                        NotificationSubscriptionRecord.enabled.is_(True),
                        or_(
                            (
                                (NotificationSubscriptionRecord.kind == SubscriptionKind.CREATOR.value)
                                & NotificationSubscriptionRecord.subject_id.in_(creator_ids)
                            ),
                            (
                                (NotificationSubscriptionRecord.kind == SubscriptionKind.RECORD.value)
                                & NotificationSubscriptionRecord.subject_id.in_(competition_ids)
                            ),
                        ),
                    )
                )
            ).scalars()
            recipients.update(subscribed)
            recipients.update(await self._matching_filter_subscribers(session, build_id, build_gains))
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
            for account_id in recipients:
                await self._insert(
                    session,
                    event=event,
                    account_id=account_id,
                    kind=NotificationKind.RECORD_GAINED,
                    source_key=f"event:{event.id}:record-build:{build_id}:account:{account_id}",
                    payload=payload,
                )

    async def _insert(
        self,
        session: AsyncSession,
        *,
        event: DomainEvent,
        account_id: int,
        kind: NotificationKind,
        source_key: str,
        payload: dict[str, object],
    ) -> None:
        row = (
            await session.execute(
                select(NotificationProfile, AccountIdentity.subject)
                .join(
                    AccountIdentity,
                    (AccountIdentity.account_id == NotificationProfile.account_id)
                    & (AccountIdentity.provider == IdentityProvider.DISCORD),
                )
                .where(
                    NotificationProfile.account_id == account_id,
                    NotificationProfile.notice_version == CURRENT_NOTIFICATION_NOTICE_VERSION,
                    NotificationProfile.consented_at.is_not(None),
                    or_(NotificationProfile.web_enabled.is_(True), NotificationProfile.dm_enabled.is_(True)),
                )
            )
        ).one_or_none()
        if row is None:
            return
        profile, discord_id = row
        notification_id = await session.scalar(
            insert(NotificationRecord)
            .values(
                account_id=account_id,
                event_id=event.id,
                source_key=source_key,
                kind=kind.value,
                payload=payload,
                web_visible=profile.web_enabled,
            )
            .on_conflict_do_nothing(index_elements=[NotificationRecord.source_key])
            .returning(NotificationRecord.id)
        )
        if (
            notification_id is None
            or not profile.dm_enabled
            or profile.dm_suspended_at is not None
            or discord_id is None
        ):
            return
        await session.execute(
            insert(NotificationDeliveryRecord)
            .values(notification_id=notification_id, account_id=account_id, discord_id=int(discord_id))
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
    async def _creator_account_ids(session: AsyncSession, build_id: int) -> Sequence[int]:
        account_ids = (
            await session.execute(
                select(CreatorAlias.account_id)
                .join(BuildCreator, BuildCreator.alias_id == CreatorAlias.id)
                .where(BuildCreator.build_id == build_id, CreatorAlias.account_id.is_not(None))
                .distinct()
            )
        ).scalars()
        return tuple(account_id for account_id in account_ids if account_id is not None)

    @staticmethod
    async def _record_gains(
        session: AsyncSession, run_id: int, previous_run_id: int
    ) -> dict[int, tuple[_RecordGain, ...]]:
        new_rows = (
            await session.execute(
                select(
                    RecordDefinition.competition_id,
                    RecordResultHolder.build_id,
                    RecordDefinition.title,
                    RecordDefinition.record_class,
                    RecordDefinition.build_kind,
                    RecordDefinition.version_scope,
                )
                .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
                .join(RecordResultHolder, RecordResultHolder.result_id == RecordResult.id)
                .where(RecordResult.run_id == run_id)
            )
        ).all()
        old_holders = set(
            (
                await session.execute(
                    select(RecordDefinition.competition_id, RecordResultHolder.build_id)
                    .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
                    .join(RecordResultHolder, RecordResultHolder.result_id == RecordResult.id)
                    .where(RecordResult.run_id == previous_run_id)
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
    async def _matching_filter_subscribers(
        session: AsyncSession, build_id: int, gains: Sequence[_RecordGain]
    ) -> set[int]:
        rows = (
            await session.execute(
                select(NotificationSubscriptionRecord.account_id, NotificationSubscriptionRecord.filter).where(
                    NotificationSubscriptionRecord.kind == SubscriptionKind.RECORD_FILTER.value,
                    NotificationSubscriptionRecord.enabled.is_(True),
                )
            )
        ).all()
        if not rows:
            return set()
        assignments = (
            await session.execute(
                select(
                    BuildTagAssignment.tag_id,
                    BuildTagAssignment.numeric_value,
                    BuildTagAssignment.text_value,
                    BuildTagAssignment.boolean_value,
                ).where(BuildTagAssignment.build_id == build_id)
            )
        ).all()
        tags = {
            tag_id: numeric if numeric is not None else text_value if text_value is not None else boolean
            for tag_id, numeric, text_value, boolean in assignments
        }
        matched: set[int] = set()
        for account_id, raw_filter in rows:
            if raw_filter is None:
                continue
            record_filter = RecordSubscriptionFilter.from_dict(dict(raw_filter))
            if any(_filter_matches(record_filter, gain, tags) for gain in gains):
                matched.add(account_id)
        return matched


def _preferences(account_id: int, profile: NotificationProfile | None) -> NotificationPreferences:
    if profile is None:
        return NotificationPreferences(account_id=account_id, notice_version=None, consented_at=None)
    return NotificationPreferences(
        account_id=account_id,
        notice_version=profile.notice_version,
        consented_at=profile.consented_at,
        web_enabled=profile.web_enabled,
        dm_enabled=profile.dm_enabled,
        dm_suspended_at=profile.dm_suspended_at,
    )


def _subscription(row: NotificationSubscriptionRecord) -> NotificationSubscription:
    return NotificationSubscription(
        id=row.id,
        account_id=row.account_id,
        kind=SubscriptionKind(row.kind),
        subject_id=row.subject_id,
        record_filter=None if row.filter is None else RecordSubscriptionFilter.from_dict(dict(row.filter)),
        created_at=row.created_at,
    )


def _inbox(row: NotificationRecord) -> InboxNotification:
    return InboxNotification(
        id=row.id,
        kind=NotificationKind(row.kind),
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


def _visibility_timeout() -> timedelta:
    return timedelta(minutes=5)


def _retry_delay(attempts: int) -> timedelta:
    return min(timedelta(seconds=15 * 2 ** max(attempts - 1, 0)), timedelta(hours=1))


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
