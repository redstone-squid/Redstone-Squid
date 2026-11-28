"""PostgreSQL notification persistence and domain-event materialization."""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, case, delete, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.infrastructure.attribution import attributed_credits
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.builds.domain import BuildStatus, CreditRole
from squid.core.builds.models import Build
from squid.core.events.models import DomainEvent, DomainEventDeliveryRecord
from squid.core.notifications.domain import (
    ALL_INBOX_ITEMS,
    InboxListFilters,
    InboxVisibility,
    NotificationCandidate,
    NotificationKind,
    NotificationPreferences,
    NotificationSource,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    SubscriptionTarget,
)
from squid.core.notifications.models import (
    InboxNotification,
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationSubscription,
)
from squid.core.pagination import Page, PageSelector
from squid.core.permissions.domain import BuiltinRoleKeys
from squid.core.permissions.models import PermissionRole, PermissionRoleAssignment
from squid.core.persistence.paging import KeysetOrder
from squid.core.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.core.records.models import (
    Record,
    RecordDefinition,
)
from squid.core.submissions.models.reviews import BuildSubmission
from squid.core.tags.models import TagAssignment, TagDefinition


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
    record_id: UUID
    build_id: int
    title: str
    criterion: str
    build_kind: str
    version_scope: str


class PostgresNotificationRepository:
    """Store notification preferences and turn domain events into recipient messages.

    Event-specific queries find candidates: build submitters, creator or record
    subscribers, and staff recipients. Candidates that pass channel
    checks become InboxNotification rows. Those rows also hold the message data
    for DMs, including when web delivery is disabled; NotificationDeliveryRecord
    tracks the separate send attempts.

    Source keys prevent an event retry from inserting the same notification twice.
    DM claims recheck channel preferences and resolve the recipient's
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
        target: SubscriptionTarget,
    ) -> NotificationSubscription | None:
        return await self._session().scalar(
            select(NotificationSubscription)
            .where(
                NotificationSubscription.account_id == account_id,
                NotificationSubscription.kind == target.kind,
                NotificationSubscription.subject_id.is_not_distinct_from(target.subject_id),
                NotificationSubscription.record_filter.is_not_distinct_from(target.record_filter),
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
        session = self._session()
        session.add(row)
        await session.flush()
        if not row.enabled:
            delivery = NotificationDeliveryRecord
            remaining = func.array_remove(delivery.subscription_ids, row.id)
            cancel = func.cardinality(remaining) == 0
            await session.execute(
                update(delivery)
                .where(
                    delivery.account_id == row.account_id,
                    delivery.sent_at.is_(None),
                    delivery.dead_at.is_(None),
                    delivery.subscription_ids.contains([row.id]),
                )
                .values(
                    subscription_ids=remaining,
                    dead_at=case((cancel, func.now()), else_=delivery.dead_at),
                    claimed_at=case((cancel, None), else_=delivery.claimed_at),
                    claim_token=case((cancel, None), else_=delivery.claim_token),
                    last_error=case((cancel, "All matching follows were removed"), else_=delivery.last_error),
                )
            )

    async def lock_inbox(
        self, account_id: int, ids: tuple[int, ...], *, visibility: InboxVisibility
    ) -> Sequence[InboxNotification]:
        return (
            await self._session().scalars(
                select(InboxNotification)
                .where(InboxNotification.id.in_(ids), *inbox_predicates(account_id, visibility))
                .order_by(InboxNotification.id)
                .with_for_update()
            )
        ).all()

    async def flush_inbox(self) -> None:
        await self._session().flush()

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        session = self._session()
        profile = await session.get(NotificationProfile, account_id)
        return _preferences(account_id, profile)

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None:
        """Set both channels, creating the profile row on first use.

        Return None when the account does not exist. Enabling
        DMs clears a previous suspension; disabling them terminates pending deliveries.
        """
        session = self._session()
        if await session.get(Account, account_id, with_for_update=True) is None:
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
        return _preferences(account_id, profile)

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool:
        session = self._session()
        if kind is SubscriptionKind.CREATOR:
            credits = attributed_credits()
            statement = select(
                exists().where(
                    Account.public_account_id == subject_id,
                    credits.c.account_id == Account.id,
                    Build.id == credits.c.build_id,
                    Build.submission_status == BuildStatus.APPROVED,
                )
            )
        else:
            statement = select(exists().where(Record.public_id == subject_id))
        return bool(await session.scalar(statement))

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

    async def subscription_labels(self, subscription_ids: Sequence[int]) -> Mapping[int, str]:
        """Name creator follows by a public alias and record follows by the latest record title."""
        if not subscription_ids:
            return {}
        latest = (
            select(RecordDefinition.record_id, RecordDefinition.title)
            .distinct(RecordDefinition.record_id)
            .order_by(RecordDefinition.record_id, RecordDefinition.id.desc())
            .subquery()
        )
        credits = attributed_credits()
        aliases = (
            select(Account.public_account_id, func.min(credits.c.name).label("name"))
            .join(credits, credits.c.account_id == Account.id)
            .join(Build, Build.id == credits.c.build_id)
            .where(Build.submission_status == BuildStatus.APPROVED)
            .group_by(Account.public_account_id)
            .subquery()
        )
        label = func.coalesce(latest.c.title, aliases.c.name)
        rows = await self._session().execute(
            select(NotificationSubscription.id, label)
            .outerjoin(
                latest,
                (NotificationSubscription.kind == SubscriptionKind.RECORD)
                & (latest.c.record_id == NotificationSubscription.subject_id),
            )
            .outerjoin(
                aliases,
                (NotificationSubscription.kind == SubscriptionKind.CREATOR)
                & (aliases.c.public_account_id == NotificationSubscription.subject_id),
            )
            .where(NotificationSubscription.id.in_(subscription_ids), label.is_not(None))
        )
        return {subscription_id: name for subscription_id, name in rows}

    async def tag_names(self, tag_ids: set[int]) -> Mapping[int, str]:
        if not tag_ids:
            return {}
        rows = await self._session().execute(
            select(TagDefinition.id, TagDefinition.display_name).where(TagDefinition.id.in_(tag_ids))
        )
        return {tag_id: name for tag_id, name in rows}

    async def list_inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector,
        page_size: int,
        visibility: InboxVisibility,
        filters: InboxListFilters = ALL_INBOX_ITEMS,
    ) -> Page[InboxNotification]:
        """List one newest-first page of web-visible inbox items."""
        statement = _inbox_filter(
            select(InboxNotification), account_id=account_id, visibility=visibility, unread_only=filters.unread_only
        )
        order = KeysetOrder("newest", InboxNotification.id, InboxNotification.id, descending=True)
        total = await self.count_inbox(account_id, visibility=visibility, unread_only=filters.unread_only)
        return await order.page(self._session(), statement, selector, page_size=page_size, total=total)

    async def count_inbox(self, account_id: int, *, visibility: InboxVisibility, unread_only: bool = False) -> int:
        """Count web-visible inbox items, mirroring the visibility rules of `list_inbox`."""
        session = self._session()
        statement = _inbox_filter(
            select(func.count()).select_from(InboxNotification),
            account_id=account_id,
            visibility=visibility,
            unread_only=unread_only,
        )
        return await session.scalar(statement) or 0

    async def unread_ids(
        self, account_id: int, *, visibility: InboxVisibility, through_id: int | None
    ) -> Sequence[int]:
        statement = _inbox_filter(
            select(InboxNotification.id), account_id=account_id, visibility=visibility, unread_only=True
        )
        if through_id is not None:
            statement = statement.where(InboxNotification.id <= through_id)
        return (await self._session().scalars(statement.order_by(InboxNotification.id))).all()

    # Create notifications from events and expire retained notifications.

    async def materialize(self, event: DomainEvent) -> None:
        session = self._session()
        if event.event_type == "build.submitted":
            await self._materialize_staff_submission(session, event)
        elif event.event_type in {"build.confirmed", "build.denied"}:
            await self._materialize_build_outcome(session, event)
        elif event.event_type == "record_run.published":
            await self._materialize_record_changes(session, event)

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

    @staticmethod
    async def _submission_link(session: AsyncSession, build_id: int) -> dict[str, str]:
        identifier = await session.scalar(
            select(BuildSubmission.id).where(
                BuildSubmission.build_id == build_id, BuildSubmission.edit_vote_id.is_(None)
            )
        )
        return {"submission_id": str(identifier)} if identifier is not None else {}

    async def _materialize_staff_submission(self, session: AsyncSession, event: DomainEvent) -> None:
        build = await session.get(Build, event.aggregate_id)
        if build is None or build.submission_status != BuildStatus.PENDING:
            return
        staff_account_ids = (
            await session.execute(
                select(Account.id).where(_is_durable_staff_notification_recipient(Account.id)).order_by(Account.id)
            )
        ).scalars()
        # Transient models can omit category.
        payload = {
            **await self._submission_link(session, build.id),
            "build_id": build.id,
            "category": (
                None
                if build.category is None  # pyright: ignore[reportUnnecessaryComparison]
                else str(build.category)
            ),
        }
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
        expected_status = BuildStatus.APPROVED if event.event_type == "build.confirmed" else BuildStatus.REJECTED
        if (
            build is None
            or build.submission_status != expected_status
            or not await self._is_latest_outcome(session, event)
        ):
            return
        kind = (
            NotificationKind.BUILD_CONFIRMED if event.event_type == "build.confirmed" else NotificationKind.BUILD_DENIED
        )
        # Every maintainer hears the outcome, matching who may edit the build (`Build.maintainer_account_ids`).
        credited = func.build_credit_accounts(build.id).table_valued("account_id", "role")
        maintainers = set(
            (
                await session.scalars(select(credited.c.account_id).where(credited.c.role == CreditRole.CREATOR.value))
            ).all()
        )
        if build.submitter_account_id is not None:
            maintainers.add(build.submitter_account_id)
        payload = {"build_id": build.id, **await self._submission_link(session, build.id)}
        candidates = [
            NotificationCandidate(
                account_id=account_id,
                kind=kind,
                source_key=NotificationSource(event.id, kind).key_for(account_id),
                payload=payload,
            )
            for account_id in sorted(maintainers)
        ]
        if event.event_type != "build.confirmed" or not await self._is_first_confirmation(session, event):
            await self._insert_candidates(session, event=event, candidates=candidates)
            return
        # Followers hear about a build's first confirmation, not every later review outcome.
        public_account_ids = tuple(await self._creator_public_ids(session, build.id))
        if public_account_ids:
            subscriptions = (
                await session.execute(
                    select(NotificationSubscription.account_id, NotificationSubscription.id).where(
                        NotificationSubscription.kind == SubscriptionKind.CREATOR,
                        NotificationSubscription.subject_id.in_(public_account_ids),
                        NotificationSubscription.enabled.is_(True),
                    )
                )
            ).all()
            follows_by_account: dict[int, list[int]] = defaultdict(list)
            for account_id, subscription_id in subscriptions:
                follows_by_account[account_id].append(subscription_id)
            candidates.extend(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.CREATOR_BUILD_CONFIRMED,
                    source_key=NotificationSource(event.id, NotificationKind.CREATOR_BUILD_CONFIRMED).key_for(
                        account_id
                    ),
                    payload={"build_id": build.id, "account_ids": [str(value) for value in public_account_ids]},
                    subscription_ids=tuple(sorted(follows)),
                )
                for account_id, follows in follows_by_account.items()
            )
        await self._insert_candidates(session, event=event, candidates=candidates)

    async def _materialize_record_changes(self, session: AsyncSession, event: DomainEvent) -> None:
        """Tell followers about records builds gained, and about records that retired.

        Gains produce one message per recipient and build, combining every record
        that build gained, including revived ones. Retirements produce one message
        per recipient listing the followed records that lost their last competitor.
        """
        # A baseline establishes existing holders; it does not represent newly won records.
        if event.payload.get("baseline") is True:
            return
        changes = _record_changes(event.payload)
        records = await self._record_facts(session, {change[0] for change in changes})
        gains: dict[int, list[_RecordGain]] = defaultdict(list)
        retired: list[tuple[_RecordGain, tuple[int, ...]]] = []
        for record_id, change, previous, holders in changes:
            facts = records.get(record_id)
            if facts is None:
                continue
            if change == "retired":
                retired.append((facts, previous))
                continue
            for build_id in holders:
                if build_id not in previous:
                    gains[build_id].append(replace(facts, build_id=build_id))
        grouped = {build_id: tuple(values) for build_id, values in gains.items()}
        recipients_by_build = await self._record_gain_recipients(session, grouped)
        candidates: list[NotificationCandidate] = []
        for build_id, build_gains in grouped.items():
            payload: dict[str, object] = {
                "build_id": build_id,
                "records": [_record_entry(gain) for gain in build_gains],
            }
            candidates.extend(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.RECORD_GAINED,
                    source_key=NotificationSource(event.id, NotificationKind.RECORD_GAINED, build_id).key_for(
                        account_id
                    ),
                    payload=payload,
                    subscription_ids=None if follows is None else tuple(sorted(follows)),
                )
                for account_id, follows in recipients_by_build[build_id].items()
            )
        for account_id, (follows, followed) in (await self._retirement_recipients(session, retired)).items():
            candidates.append(
                NotificationCandidate(
                    account_id=account_id,
                    kind=NotificationKind.RECORD_RETIRED,
                    source_key=NotificationSource(event.id, NotificationKind.RECORD_RETIRED).key_for(account_id),
                    payload={"records": [_record_entry(record) for record in followed]},
                    subscription_ids=tuple(sorted(follows)),
                )
            )
        await self._insert_candidates(session, event=event, candidates=candidates)

    @staticmethod
    async def _record_facts(session: AsyncSession, record_ids: set[UUID]) -> dict[UUID, _RecordGain]:
        """Name each record by its newest definition; `build_id` is filled in per gain."""
        if not record_ids:
            return {}
        title = (
            select(RecordDefinition.title)
            .where(RecordDefinition.record_id == Record.public_id)
            .order_by(RecordDefinition.id.desc())
            .limit(1)
            .correlate(Record)
            .scalar_subquery()
        )
        rows = await session.execute(
            select(Record.public_id, title, Record.criterion, Record.build_kind, Record.version_scope).where(
                Record.public_id.in_(record_ids)
            )
        )
        return {
            record_id: _RecordGain(record_id, 0, record_title or "", criterion, build_kind, version_scope)
            for record_id, record_title, criterion, build_kind, version_scope in rows
        }

    async def _retirement_recipients(
        self, session: AsyncSession, retired: Sequence[tuple[_RecordGain, tuple[int, ...]]]
    ) -> dict[int, tuple[set[int], list[_RecordGain]]]:
        """Collect each follower of a retired record, with the follows and records that matched.

        A filter's tag predicates are checked against the record's former holders.
        """
        recipients: dict[int, tuple[set[int], list[_RecordGain]]] = {}
        if not retired:
            return recipients

        def add(account_id: int, subscription_id: int, record: _RecordGain) -> None:
            follows, records = recipients.setdefault(account_id, (set(), []))
            follows.add(subscription_id)
            if record not in records:
                records.append(record)

        by_id = {record.record_id: record for record, _previous in retired}
        exact = await session.execute(
            select(
                NotificationSubscription.account_id, NotificationSubscription.subject_id, NotificationSubscription.id
            ).where(
                NotificationSubscription.kind == SubscriptionKind.RECORD,
                NotificationSubscription.subject_id.in_(by_id),
                NotificationSubscription.enabled.is_(True),
            )
        )
        for account_id, subject_id, subscription_id in exact:
            if subject_id is not None:
                add(account_id, subscription_id, by_id[subject_id])
        filters = (
            await session.execute(
                select(
                    NotificationSubscription.account_id,
                    NotificationSubscription.record_filter,
                    NotificationSubscription.id,
                ).where(
                    NotificationSubscription.kind == SubscriptionKind.RECORD_FILTER,
                    NotificationSubscription.enabled.is_(True),
                )
            )
        ).all()
        if filters:
            tags = await _build_tags(session, {build_id for _record, previous in retired for build_id in previous})
            for account_id, record_filter, subscription_id in filters:
                if record_filter is None:
                    continue
                for record, previous in retired:
                    if any(_filter_matches(record_filter, record, tags[build_id]) for build_id in previous):
                        add(account_id, subscription_id, record)
        return recipients

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
        follow_ids = {follow_id for candidate in candidates for follow_id in candidate.subscription_ids or ()}
        if follow_ids:
            # Serialize queue insertion with settings edits, then recheck the matches
            # found before taking the locks.
            await session.execute(
                select(Account.id).where(Account.id.in_(account_ids)).order_by(Account.id).with_for_update()
            )
            subscriptions = await session.execute(
                select(NotificationSubscription.account_id, NotificationSubscription.id).where(
                    NotificationSubscription.id.in_(follow_ids), NotificationSubscription.enabled.is_(True)
                )
            )
            active_follows = {(account_id, follow_id) for account_id, follow_id in subscriptions}
            candidates = [
                candidate
                if candidate.subscription_ids is None
                else replace(
                    candidate,
                    subscription_ids=tuple(
                        follow_id
                        for follow_id in candidate.subscription_ids
                        if (candidate.account_id, follow_id) in active_follows
                    ),
                )
                for candidate in candidates
            ]
        candidates = [candidate for candidate in candidates if candidate.subscription_ids != ()]
        profiles = {
            profile.account_id: profile
            for profile in (
                await session.scalars(
                    select(NotificationProfile)
                    .join(Account, Account.id == NotificationProfile.account_id)
                    .where(
                        NotificationProfile.account_id.in_(account_ids),
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
        delivery_columns = ["notification_id", "account_id"]
        tracked = {
            candidate.source_key: literal(
                list(candidate.subscription_ids), NotificationDeliveryRecord.subscription_ids.type
            )
            for candidate in candidates
            if candidate.subscription_ids is not None
        }
        if tracked:
            delivery_columns.append("subscription_ids")
            deliverable = deliverable.add_columns(case(tracked, value=InboxNotification.source_key))
        await session.execute(
            insert(NotificationDeliveryRecord)
            .from_select(
                delivery_columns,
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
        credits = attributed_credits()
        return tuple(
            (
                await session.execute(
                    select(Account.public_account_id)
                    .join(credits, credits.c.account_id == Account.id)
                    .where(credits.c.build_id == build_id)
                    .distinct()
                )
            ).scalars()
        )

    @staticmethod
    async def _record_gain_recipients(
        session: AsyncSession,
        gains: dict[int, tuple[_RecordGain, ...]],
    ) -> dict[int, dict[int, set[int] | None]]:
        """Collect matching follows per build and recipient, using None for credited creators.

        Matching any gained record qualifies a subscriber. The resulting message
        includes all of that build's gains, even when only one matched the filter.
        """
        build_ids = set(gains)
        recipients: dict[int, dict[int, set[int] | None]] = {build_id: {} for build_id in build_ids}
        if not build_ids:
            return recipients
        credits = func.build_credit_accounts().table_valued("build_id", "position", "account_id", "name", "role")
        creator_rows = (
            await session.execute(
                select(credits.c.build_id, Account.id, Account.public_account_id)
                .select_from(credits)
                .join(Account, Account.id == credits.c.account_id)
                .where(credits.c.build_id.in_(build_ids))
            )
        ).all()
        for build_id, account_id, _ in creator_rows:
            recipients[build_id][account_id] = None
        visible = attributed_credits()
        public_account_ids: dict[int, set[UUID]] = defaultdict(set)
        for build_id, public_id in await session.execute(
            select(visible.c.build_id, Account.public_account_id)
            .join(Account, Account.id == visible.c.account_id)
            .where(visible.c.build_id.in_(build_ids))
            .distinct()
        ):
            public_account_ids[build_id].add(public_id)

        relevant_subjects: set[UUID] = set().union(*public_account_ids.values())
        relevant_subjects.update(gain.record_id for build_gains in gains.values() for gain in build_gains)
        exact_subscriptions = (
            await session.execute(
                select(
                    NotificationSubscription.account_id,
                    NotificationSubscription.kind,
                    NotificationSubscription.subject_id,
                    NotificationSubscription.id,
                ).where(
                    NotificationSubscription.kind.in_((SubscriptionKind.CREATOR, SubscriptionKind.RECORD)),
                    NotificationSubscription.subject_id.in_(relevant_subjects),
                    NotificationSubscription.enabled.is_(True),
                )
            )
        ).all()
        for account_id, kind, subject_id, subscription_id in exact_subscriptions:
            for build_id, build_gains in gains.items():
                subjects = (
                    public_account_ids[build_id]
                    if kind is SubscriptionKind.CREATOR
                    else {gain.record_id for gain in build_gains}
                )
                if subject_id in subjects:
                    follows = recipients[build_id].setdefault(account_id, set())
                    if follows is not None:
                        follows.add(subscription_id)

        filter_rows = (
            await session.execute(
                select(
                    NotificationSubscription.account_id,
                    NotificationSubscription.record_filter,
                    NotificationSubscription.id,
                ).where(
                    NotificationSubscription.kind == SubscriptionKind.RECORD_FILTER,
                    NotificationSubscription.enabled.is_(True),
                )
            )
        ).all()
        tags_by_build = await _build_tags(session, build_ids)
        for account_id, raw_filter, subscription_id in filter_rows:
            if raw_filter is None:
                continue
            record_filter = raw_filter
            for build_id, build_gains in gains.items():
                if any(_filter_matches(record_filter, gain, tags_by_build[build_id]) for gain in build_gains):
                    follows = recipients[build_id].setdefault(account_id, set())
                    if follows is not None:
                        follows.add(subscription_id)
        return recipients


async def _build_tags(
    session: AsyncSession, build_ids: set[int]
) -> defaultdict[int, dict[int, Decimal | str | bool | None]]:
    """Each build's tag values by tag ID; a tag without a value maps to None."""
    tags_by_build: defaultdict[int, dict[int, Decimal | str | bool | None]] = defaultdict(dict)
    if not build_ids:
        return tags_by_build
    assignments = await session.execute(
        select(
            TagAssignment.build_id,
            TagAssignment.tag_id,
            TagAssignment.numeric_value,
            TagAssignment.text_value,
            TagAssignment.boolean_value,
        ).where(TagAssignment.build_id.in_(build_ids))
    )
    for build_id, tag_id, numeric, text_value, boolean in assignments:
        tags_by_build[build_id][tag_id] = (
            numeric if numeric is not None else text_value if text_value is not None else boolean
        )
    return tags_by_build


def _record_changes(payload: dict[str, object]) -> list[tuple[UUID, str, tuple[int, ...], tuple[int, ...]]]:
    """Parse `record_run.published` changes, skipping malformed entries."""
    raw = payload.get("changes")
    changes: list[tuple[UUID, str, tuple[int, ...], tuple[int, ...]]] = []
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict):
            continue
        try:
            record_id = UUID(str(entry["record_id"]))
        except KeyError, ValueError:
            continue
        ids = [
            tuple(value for value in values if type(value) is int) if isinstance(values := entry.get(key), list) else ()
            for key in ("previous_holder_ids", "holder_ids")
        ]
        changes.append((record_id, str(entry.get("change")), ids[0], ids[1]))
    return changes


def _record_entry(record: _RecordGain) -> dict[str, object]:
    return {
        "title_id": str(record.record_id),
        "title": record.title,
        "base_title": record.criterion,
        "build_kind": record.build_kind,
        "version_scope": record.version_scope,
    }


def _preferences(account_id: int, profile: NotificationProfile | None) -> NotificationPreferences:
    if profile is None:
        return NotificationPreferences(account_id=account_id)
    return NotificationPreferences(
        account_id=account_id,
        revision=profile.revision,
        web_enabled=profile.web_enabled,
        dm_enabled=profile.dm_enabled,
        dm_suspended_at=profile.dm_suspended_at,
    )


def inbox_predicates(account_id: int, visibility: InboxVisibility) -> tuple[ColumnElement[bool], ...]:
    predicates = (
        InboxNotification.account_id == account_id,
        InboxNotification.web_visible.is_(True),
        exists().where(
            NotificationProfile.account_id == account_id,
            NotificationProfile.web_enabled.is_(True),
            Account.id == NotificationProfile.account_id,
        ),
    )
    if visibility.include_staff:
        return predicates
    return (*predicates, InboxNotification.kind != NotificationKind.STAFF_BUILD_SUBMITTED)


def _inbox_filter[S: Select[Any]](
    statement: S, *, account_id: int, visibility: InboxVisibility, unread_only: bool = False
) -> S:
    """Apply the visibility rules shared by inbox reads and state transitions."""
    statement = statement.where(*inbox_predicates(account_id, visibility))
    return statement.where(InboxNotification.read_at.is_(None)) if unread_only else statement


def _filter_matches(
    record_filter: RecordSubscriptionFilter,
    gain: _RecordGain,
    tags: dict[int, Decimal | str | bool | None],
) -> bool:
    if record_filter.build_kinds and gain.build_kind not in record_filter.build_kinds:
        return False
    if record_filter.criteria and gain.criterion not in record_filter.criteria:
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
