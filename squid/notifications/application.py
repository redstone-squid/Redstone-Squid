"""Notification preference, subscription, and inbox orchestration."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.events import DomainEvent
from squid.notifications.domain import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.settings.domain.runtime import RuntimeSettings
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class NotificationEditorSnapshot:
    preferences: NotificationPreferences
    subscriptions: tuple[NotificationSubscription, ...]


class NotificationRepository(Protocol):
    """Persistence needed by the notification application service."""

    async def lock_settings(self, account_id: int) -> int: ...

    async def matching_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription | None: ...

    async def get_preferences(self, account_id: int) -> NotificationPreferences: ...

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None: ...

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool: ...

    async def add_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription: ...

    async def list_subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]: ...

    async def delete_subscription(self, account_id: int, subscription_id: int) -> bool: ...

    async def list_inbox(
        self,
        account_id: int,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        limit: int,
        visibility: InboxVisibility,
    ) -> Sequence[InboxNotification]: ...

    async def count_inbox(self, account_id: int, *, visibility: InboxVisibility) -> int: ...

    async def mark_read(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool: ...

    async def mark_unread(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool: ...

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

    Inbox visibility is supplied by the authenticated caller's adapter; staff
    messages require its permission check as well as the account's preferences.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[NotificationUnitOfWork],
        retention_days: int = 90,
        runtime_settings: Callable[[], Awaitable[RuntimeSettings]] | None = None,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"notifications-retention-days-positive = retention_days must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._retention_days = retention_days
        self._runtime_settings = runtime_settings

    async def editor_snapshot(self, account_id: int) -> NotificationEditorSnapshot:
        async with self._uow_factory() as uow:
            await uow.notifications.lock_settings(account_id)
            return NotificationEditorSnapshot(
                await uow.notifications.get_preferences(account_id),
                tuple(await uow.notifications.list_subscriptions(account_id)),
            )

    async def subscription_snapshot(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> tuple[NotificationSubscription | None, int]:
        """Find a stable follow identity, including one disabled since a lost creation response."""
        async with self._uow_factory() as uow:
            revision = await uow.notifications.lock_settings(account_id)
            row = await uow.notifications.matching_subscription(
                account_id,
                kind=kind,
                subject_id=subject_id,
                record_filter=record_filter,
            )
            return row, revision

    async def preferences(self, account_id: int) -> NotificationPreferences:
        """Return preferences, including an implicit disabled profile when absent."""
        async with self._uow_factory() as uow:
            return await uow.notifications.get_preferences(account_id)

    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        """List the caller's enabled subscriptions."""
        async with self._uow_factory() as uow:
            return await uow.notifications.list_subscriptions(account_id)

    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> Page[InboxNotification]:
        """Return one page of web-visible inbox items in newest-first display order."""
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
            )
            return keyset_page(
                rows,
                selector=selector,
                page_size=page_size,
                total=await uow.notifications.count_inbox(account_id, visibility=visibility),
                keyset=True,
                id_of=lambda item: item.id,
            )

    async def materialize(self, event: DomainEvent) -> None:
        """Create eligible recipients' stored notifications and queue their DMs.

        Existing source keys are reused when the event is delivered again.
        """
        async with self._uow_factory() as uow:
            await uow.notifications.materialize(event)
            await uow.commit()

    async def cleanup(self) -> int:
        """Remove expired inbox items and return their count.

        Old source events are also removed when no inbox item or event-delivery
        record still references them.
        """
        retention_days = (
            (await self._runtime_settings()).notification_retention_days
            if self._runtime_settings is not None
            else self._retention_days
        )
        async with self._uow_factory() as uow:
            result = await uow.notifications.cleanup(retention_days=retention_days)
            await uow.commit()
            return result

    async def claim_deliveries(self, *, limit: int = 20) -> Sequence[PendingNotificationDelivery]:
        """Reserve ready Discord DMs and commit their claim tokens before returning."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= 100:
                msg = tr(t"notifications-delivery-claim-limit = delivery claim limit must be between 1 and 100")
                raise InvalidStateError(msg)
            result = await uow.notifications.claim_deliveries(limit=limit)
            await uow.commit()
            return result

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        """Mark a DM sent only while this claim still owns its generation."""
        async with self._uow_factory() as uow:
            result = await uow.notifications.complete_delivery(delivery)
            await uow.commit()
            return result

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Retry a failed send, or stop after eight attempts; return whether it became dead.

        Stale claims do nothing and return False. A timeout may occur after Discord
        accepted the message, so retrying can produce a duplicate.
        """
        async with self._uow_factory() as uow:
            result = await uow.notifications.fail_delivery(delivery, str(error)[:4000], max_attempts=8)
            await uow.commit()
            return result

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Disable DMs after Discord rejects a send, returning False if its claim is stale."""
        async with self._uow_factory() as uow:
            result = await uow.notifications.suspend_dm(delivery, str(error)[:4000])
            await uow.commit()
            return result


__all__ = ["NotificationRepository", "NotificationService"]


class NotificationUnitOfWork(UnitOfWork, Protocol):
    @property
    def notifications(self) -> NotificationRepository: ...
