"""Notification preference, subscription, and inbox orchestration."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from squid.accounts.errors import ConsentRequiredError
from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.events import DomainEvent
from squid.notifications.domain import (
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.errors import NotificationSubscriptionNotFoundError


class NotificationRepository(Protocol):
    """Persistence needed by the notification application service."""

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        """Return the account's switches, or an all-off profile when it has no row."""
        ...

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None:
        """Upsert both switches; None when the account has not accepted the privacy notice.

        Turning DMs off kills the account's unsent deliveries; turning them on clears a suspension.
        """
        ...

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool:
        """Whether a creator (`CREATOR`) or record competition (`RECORD`) with this public id exists."""
        ...

    async def add_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription:
        """Return the account's equivalent enabled subscription, inserting it when absent."""
        ...

    async def list_subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        """Enabled subscriptions, oldest first."""
        ...

    async def delete_subscription(self, account_id: int, subscription_id: int) -> bool:
        """False when no such subscription belongs to the account."""
        ...

    async def list_inbox(
        self,
        account_id: int,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        limit: int,
        include_staff: bool,
    ) -> Sequence[InboxNotification]:
        """Newest-first web-visible items; empty when the web inbox is off.

        `STAFF_BUILD_SUBMITTED` items appear only with `include_staff`. A `before_id` page carries
        its overfetched row at the front rather than the back.
        """
        ...

    async def count_inbox(self, account_id: int, *, include_staff: bool) -> int:
        """Count under the visibility rules of `list_inbox`."""
        ...

    async def mark_read(self, account_id: int, notification_id: int, *, include_staff: bool) -> bool:
        """False when the item is not visible to the account; an already-read item keeps its `read_at`."""
        ...

    async def materialize(self, event: DomainEvent) -> None:
        """Project one event into inbox rows and DM deliveries; a redelivered event inserts nothing."""
        ...

    async def cleanup(self, *, retention_days: int) -> int:
        """Delete inbox items older than `retention_days` and unreferenced source events; return the item count."""
        ...

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]:
        """Claim up to `limit` ready DMs, skipping rows another worker holds and reclaiming expired claims."""
        ...

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        """Mark sent; False when the claim has been superseded."""
        ...

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool:
        """Reschedule with backoff, or mark dead once `attempts` reaches `max_attempts`; True only when dead."""
        ...

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool:
        """Mark dead and switch the recipient's DMs off; False when the claim has been superseded."""
        ...


class NotificationService:
    """Raises `InvalidStateError` when `retention_days` is below 1."""

    def __init__(self, repository: NotificationRepository, *, retention_days: int = 90) -> None:
        if retention_days < 1:
            msg = tr(t"retention_days must be positive")
            raise InvalidStateError(msg)
        self._repository = repository
        self._retention_days = retention_days

    async def preferences(self, account_id: int) -> NotificationPreferences:
        """Return preferences, including an implicit disabled profile when absent."""
        return await self._repository.get_preferences(account_id)

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        """Set both channels; raises `ConsentRequiredError` until the account accepts the privacy notice."""
        preferences = await self._repository.update_preferences(
            account_id,
            web_enabled=web_enabled,
            dm_enabled=dm_enabled,
        )
        if preferences is None:
            raise ConsentRequiredError(account_id=account_id)
        return preferences

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        """Create or return one equivalent enabled subscription.

        Raises:
            ConsentRequiredError: The account has not accepted the privacy notice.
            ValidationError: `subject_id` and `record_filter` do not match `kind` (a filter alone for
                `RECORD_FILTER`, a subject alone otherwise).
            NotificationSubscriptionNotFoundError: No creator or record competition has `subject_id`.
        """
        preferences = await self._repository.get_preferences(account_id)
        if not preferences.has_current_consent:
            raise ConsentRequiredError(account_id=account_id)
        if kind is SubscriptionKind.RECORD_FILTER:
            if subject_id is not None or record_filter is None:
                msg = tr(t"record_filter subscriptions require only a structured filter")
                raise ValidationError(msg)
        elif subject_id is None or record_filter is not None:
            msg = tr(t"creator and record subscriptions require only a subject_id")
            raise ValidationError(msg)
        elif not await self._repository.subscription_target_exists(kind, subject_id):
            raise NotificationSubscriptionNotFoundError(public_context={"subject_id": str(subject_id)})
        return await self._repository.add_subscription(
            account_id,
            kind=kind,
            subject_id=subject_id,
            record_filter=record_filter,
        )

    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        return await self._repository.list_subscriptions(account_id)

    async def unsubscribe(self, account_id: int, subscription_id: int) -> None:
        """Raises `NotificationSubscriptionNotFoundError` when the subscription is not the caller's."""
        if not await self._repository.delete_subscription(account_id, subscription_id):
            raise NotificationSubscriptionNotFoundError(public_context={"subscription_id": subscription_id})

    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        include_staff: bool = False,
    ) -> Page[InboxNotification]:
        """Return one newest-first page of web-visible items.

        Raises `InvalidStateError` unless 1 <= page_size <= 100.
        """
        if not 1 <= page_size <= 100:
            msg = tr(t"page_size must be between 1 and 100")
            raise InvalidStateError(msg)
        rows = await self._repository.list_inbox(
            account_id,
            offset=selector.offset,
            after_id=selector.after_id,
            before_id=selector.before_id,
            # One row past the page proves whether another page follows.
            limit=page_size + 1,
            include_staff=include_staff,
        )
        return keyset_page(
            rows,
            selector=selector,
            page_size=page_size,
            total=await self._repository.count_inbox(account_id, include_staff=include_staff),
            keyset=True,
            id_of=lambda item: item.id,
        )

    async def mark_read(self, account_id: int, notification_id: int, *, include_staff: bool = False) -> None:
        """Raises `NotificationSubscriptionNotFoundError` when the item is not visible to the caller."""
        if not await self._repository.mark_read(account_id, notification_id, include_staff=include_staff):
            raise NotificationSubscriptionNotFoundError(
                resource="notification", public_context={"notification_id": notification_id}
            )

    async def materialize(self, event: DomainEvent) -> None:
        """Project one event into inbox and DM work; safe on redelivery."""
        await self._repository.materialize(event)

    async def cleanup(self) -> int:
        """Remove inbox and source events older than the configured retention window."""
        return await self._repository.cleanup(retention_days=self._retention_days)

    async def claim_deliveries(self, *, limit: int = 20) -> Sequence[PendingNotificationDelivery]:
        """Claim ready DMs with fresh fencing tokens; raises `InvalidStateError` unless 1 <= limit <= 100."""
        if not 1 <= limit <= 100:
            msg = tr(t"delivery claim limit must be between 1 and 100")
            raise InvalidStateError(msg)
        return await self._repository.claim_deliveries(limit=limit)

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        """Mark a DM sent; False when this claim no longer owns the row."""
        return await self._repository.complete_delivery(delivery)

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Retry with backoff, or mark dead after 8 attempts (True).

        A timed-out send may already have arrived, so a retry can duplicate the DM.
        """
        return await self._repository.fail_delivery(delivery, str(error)[:4000], max_attempts=8)

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Disable DMs after Discord explicitly rejects messages to the recipient."""
        return await self._repository.suspend_dm(delivery, str(error)[:4000])


__all__ = ["NotificationRepository", "NotificationService"]
