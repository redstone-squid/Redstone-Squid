"""Notification preference, subscription, and inbox orchestration."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.actors import ActingContext, application_operation
from squid.core.builds.models import Build
from squid.core.errors import InvalidStateError
from squid.core.events import DomainEvent
from squid.core.i18n import tr
from squid.core.notifications.domain import (
    InboxVisibility,
    NotificationMessage,
    NotificationPreferences,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    SubscriptionTarget,
)
from squid.core.notifications.models import InboxNotification, NotificationSubscription
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import ACCOUNT_SELF_READ, BUILD_SUBMISSION_VIEW_PENDING
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory
from squid.core.worker_context import WorkerOperation, worker_scope
from squid_i18n import Text


@dataclass(frozen=True, slots=True)
class FollowedSubject:
    """A subscription with the names a reader needs to recognize it.

    `label` is the creator's alias or the record's latest title. It is None for
    filters, which have `filter_summary` instead, and for subjects that no longer exist.
    """

    subscription: NotificationSubscription
    label: str | None = None
    filter_summary: Text | None = None

    @property
    def href(self) -> str | None:
        """The site path of the followed creator or record."""
        match self.subscription.kind:
            case SubscriptionKind.CREATOR if self.label is not None:
                return f"/accounts/{self.subscription.subject_id}"
            case SubscriptionKind.RECORD:
                return f"/records/{self.subscription.subject_id}"
            case _:
                return None


@dataclass(frozen=True, slots=True)
class NotificationEditorSnapshot:
    preferences: NotificationPreferences
    follows: tuple[FollowedSubject, ...]


@dataclass(frozen=True, slots=True)
class InboxEntry:
    """One web-visible notification, naming its build by the build's current title."""

    id: int
    revision: int
    created_at: Instant
    read_at: Instant | None
    message: NotificationMessage


@dataclass(frozen=True, slots=True)
class Inbox:
    """One inbox page and the number of unread items in the whole inbox."""

    page: Page[InboxEntry]
    unread_count: int


class NotificationBuildReader(Protocol):
    async def get_many(self, build_ids: Sequence[int]) -> Sequence[Build]: ...


class NotificationRepository(Protocol):
    """Persistence needed by the notification application service."""

    async def lock_settings(self, account_id: int) -> int: ...

    async def matching_subscription(
        self,
        account_id: int,
        target: SubscriptionTarget,
    ) -> NotificationSubscription | None: ...

    async def get_preferences(self, account_id: int) -> NotificationPreferences: ...

    async def list_subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]: ...

    async def subscription_labels(self, subscription_ids: Sequence[int]) -> Mapping[int, str]: ...

    async def tag_names(self, tag_ids: set[int]) -> Mapping[int, str]: ...

    async def list_inbox(
        self,
        account_id: int,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        limit: int,
        visibility: InboxVisibility,
        unread_only: bool = False,
    ) -> Sequence[InboxNotification]: ...

    async def count_inbox(self, account_id: int, *, visibility: InboxVisibility, unread_only: bool = False) -> int: ...

    async def unread_ids(
        self, account_id: int, *, visibility: InboxVisibility, through_id: int | None
    ) -> Sequence[int]: ...

    async def materialize(self, event: DomainEvent) -> None: ...

    async def cleanup(self, *, retention_days: int) -> int: ...

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]: ...

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool: ...

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool: ...

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool: ...


class NotificationService:
    """Manage notification preferences, subscriptions, inbox access, and DM attempts.

    `materialize` turns a domain event into stored notifications for eligible
    recipients and queues their DMs. Repeated events reuse the same source keys.
    The bot claims queued deliveries, sends them through Discord, and reports
    success or failure here. Every write commits before returning, so the bot
    holds no database transaction open while sending a message.

    Inbox reads derive the account and staff visibility from the effective actor.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[NotificationUnitOfWork],
        permissions: PermissionService,
        retention_days: int = 90,
        runtime_settings: WorkerOperation[RuntimeSettings] | None = None,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"notifications-retention-days-positive = retention_days must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._permissions = permissions
        self._retention_days = retention_days
        self._runtime_settings = runtime_settings

    async def _reader_account(self, context: ActingContext) -> int:
        account_id = context.effective.require_account_id()
        await self._permissions.require(context.effective, ACCOUNT_SELF_READ)
        return account_id

    async def _inbox_visibility(self, context: ActingContext) -> InboxVisibility:
        return InboxVisibility(
            include_staff=(await self._permissions.check(context.effective, BUILD_SUBMISSION_VIEW_PENDING)).allowed
        )

    @staticmethod
    async def _describe(
        uow: NotificationUnitOfWork, rows: Sequence[NotificationSubscription]
    ) -> tuple[FollowedSubject, ...]:
        labels = await uow.notifications.subscription_labels([row.id for row in rows])
        tag_names = await uow.notifications.tag_names(
            {predicate.tag_id for row in rows if row.record_filter is not None for predicate in row.record_filter.tags}
        )
        return tuple(
            FollowedSubject(
                row,
                labels.get(row.id),
                None if row.record_filter is None else row.record_filter.summary(tag_names),
            )
            for row in rows
        )

    @application_operation
    async def editor_snapshot(self, *, context: ActingContext) -> NotificationEditorSnapshot:
        account_id = await self._reader_account(context)
        async with self._uow_factory() as uow:
            await uow.notifications.lock_settings(account_id)
            return NotificationEditorSnapshot(
                await uow.notifications.get_preferences(account_id),
                await self._describe(uow, await uow.notifications.list_subscriptions(account_id)),
            )

    @application_operation
    async def subscription_snapshot(
        self,
        *,
        context: ActingContext,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> tuple[FollowedSubject | None, int]:
        """Find a stable follow identity, including one disabled since a lost creation response."""
        account_id = await self._reader_account(context)
        async with self._uow_factory() as uow:
            revision = await uow.notifications.lock_settings(account_id)
            row = await uow.notifications.matching_subscription(
                account_id,
                SubscriptionTarget(kind, subject_id, record_filter),
            )
            return (None if row is None else (await self._describe(uow, (row,)))[0]), revision

    @application_operation
    async def preferences(self, *, context: ActingContext) -> NotificationPreferences:
        """Return preferences, including an implicit disabled profile when absent."""
        account_id = await self._reader_account(context)
        async with self._uow_factory() as uow:
            return await uow.notifications.get_preferences(account_id)

    @application_operation
    async def subscriptions(self, *, context: ActingContext) -> Sequence[NotificationSubscription]:
        """List the caller's enabled subscriptions."""
        account_id = await self._reader_account(context)
        async with self._uow_factory() as uow:
            return await uow.notifications.list_subscriptions(account_id)

    @application_operation
    async def inbox(
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> Inbox:
        """Return one page of web-visible inbox items in newest-first display order."""
        account_id = await self._reader_account(context)
        visibility = await self._inbox_visibility(context)
        async with self._uow_factory() as uow:
            if not 1 <= page_size <= 100:
                msg = tr(t"notifications-page-size = page_size must be between 1 and 100")
                raise InvalidStateError(msg)
            rows = await uow.notifications.list_inbox(
                account_id,
                offset=selector.offset,
                after_id=selector.after_id,
                before_id=selector.before_id,
                # One row past the page proves whether another page follows.
                limit=page_size + 1,
                visibility=visibility,
                unread_only=unread_only,
            )
            total = await uow.notifications.count_inbox(account_id, visibility=visibility, unread_only=unread_only)
            unread = (
                total
                if unread_only
                else await uow.notifications.count_inbox(account_id, visibility=visibility, unread_only=True)
            )
            page = keyset_page(rows, selector=selector, page_size=page_size, total=total, id_of=lambda item: item.id)
            build_ids = sorted({build_id for row in page.items if type(build_id := row.payload.get("build_id")) is int})
            titles = {build.id: build.one_line_title for build in await uow.builds.get_many(build_ids)}
        entries = tuple(
            InboxEntry(
                row.id,
                row.revision,
                row.created_at,
                row.read_at,
                NotificationMessage.from_payload(row.kind, row.payload, titles),
            )
            for row in page.items
        )
        return Inbox(Page(entries, page.total, page.next, page.prev), unread)

    @application_operation
    async def unread_ids(self, *, context: ActingContext, through_id: int | None = None) -> Sequence[int]:
        """List visible unread items, leaving out those newer than `through_id`."""
        account_id = await self._reader_account(context)
        visibility = await self._inbox_visibility(context)
        async with self._uow_factory() as uow:
            return await uow.notifications.unread_ids(account_id, visibility=visibility, through_id=through_id)

    async def materialize(self, event: DomainEvent, *, context: ActingContext) -> None:
        """Create eligible recipients' stored notifications and queue their DMs.

        Existing source keys are reused when the event is delivered again.
        """
        with worker_scope(context):
            async with self._uow_factory() as uow:
                await uow.notifications.materialize(event)
                await uow.commit()

    async def cleanup(self, *, context: ActingContext) -> int:
        """Remove expired inbox items and return their count.

        Old source events are also removed when no inbox item or event-delivery
        record still references them.
        """
        with worker_scope(context):
            retention_days = (
                (await self._runtime_settings(context=context)).notification_retention_days
                if self._runtime_settings is not None
                else self._retention_days
            )
            async with self._uow_factory() as uow:
                result = await uow.notifications.cleanup(retention_days=retention_days)
                await uow.commit()
                return result

    async def claim_deliveries(
        self, *, context: ActingContext, limit: int = 20
    ) -> Sequence[PendingNotificationDelivery]:
        """Reserve ready Discord DMs and commit their claim tokens before returning."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                if not 1 <= limit <= 100:
                    msg = tr(t"notifications-delivery-claim-limit = delivery claim limit must be between 1 and 100")
                    raise InvalidStateError(msg)
                result = await uow.notifications.claim_deliveries(limit=limit)
                await uow.commit()
                return result

    async def complete_delivery(self, delivery: PendingNotificationDelivery, *, context: ActingContext) -> bool:
        """Mark a DM sent only while this claim still owns its generation."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                result = await uow.notifications.complete_delivery(delivery)
                await uow.commit()
                return result

    async def fail_delivery(
        self, delivery: PendingNotificationDelivery, error: Exception, *, context: ActingContext
    ) -> bool:
        """Retry a failed send, or stop after eight attempts; return whether it became dead.

        Stale claims do nothing and return False. A timeout may occur after Discord
        accepted the message, so retrying can produce a duplicate.
        """
        with worker_scope(context):
            async with self._uow_factory() as uow:
                result = await uow.notifications.fail_delivery(delivery, str(error)[:4000], max_attempts=8)
                await uow.commit()
                return result

    async def suspend_dm(
        self, delivery: PendingNotificationDelivery, error: Exception, *, context: ActingContext
    ) -> bool:
        """Disable DMs after Discord rejects a send, returning False if its claim is stale."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                result = await uow.notifications.suspend_dm(delivery, str(error)[:4000])
                await uow.commit()
                return result


__all__ = ["FollowedSubject", "Inbox", "InboxEntry", "NotificationRepository", "NotificationService"]


class NotificationUnitOfWork(UnitOfWork, Protocol):
    @property
    def notifications(self) -> NotificationRepository: ...

    @property
    def builds(self) -> NotificationBuildReader: ...
