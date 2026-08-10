"""Notification preference, subscription, and inbox orchestration."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from squid.events import DomainEvent
from squid.notifications.domain import (
    CURRENT_NOTIFICATION_NOTICE_VERSION,
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.errors import NotificationConsentRequiredError, NotificationSubscriptionNotFoundError


class NotificationRepository(Protocol):
    """Persistence needed by the notification application service."""

    async def get_preferences(self, account_id: int) -> NotificationPreferences: ...

    async def accept_notice(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences: ...

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
        self, account_id: int, *, after_id: int | None, limit: int, include_staff: bool
    ) -> Sequence[InboxNotification]: ...

    async def mark_read(self, account_id: int, notification_id: int, *, include_staff: bool) -> bool: ...

    async def materialize(self, event: DomainEvent) -> None: ...

    async def cleanup(self, *, retention_days: int) -> int: ...

    async def can_view_staff(self, discord_id: int) -> bool: ...

    async def claim_deliveries(self, *, limit: int) -> Sequence[PendingNotificationDelivery]: ...

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool: ...

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool: ...

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool: ...


class NotificationService:
    """Manage opt-in notification state and idempotent event materialization."""

    def __init__(self, repository: NotificationRepository, *, retention_days: int = 90) -> None:
        if retention_days < 1:
            msg = "retention_days must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._retention_days = retention_days

    async def preferences(self, account_id: int) -> NotificationPreferences:
        """Return preferences, including an implicit disabled profile when absent."""
        return await self._repository.get_preferences(account_id)

    async def accept_notice(
        self, account_id: int, *, web_enabled: bool = False, dm_enabled: bool = False
    ) -> NotificationPreferences:
        """Record the current notification notice and initial channel choices."""
        return await self._repository.accept_notice(account_id, web_enabled=web_enabled, dm_enabled=dm_enabled)

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        """Change channels only after the notification-specific notice is current."""
        preferences = await self._repository.update_preferences(
            account_id,
            web_enabled=web_enabled,
            dm_enabled=dm_enabled,
        )
        if preferences is None:
            raise NotificationConsentRequiredError
        return preferences

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        """Create or return one equivalent enabled subscription."""
        preferences = await self._repository.get_preferences(account_id)
        if not preferences.has_current_consent:
            raise NotificationConsentRequiredError
        if kind is SubscriptionKind.RECORD_FILTER:
            if subject_id is not None or record_filter is None:
                msg = "record_filter subscriptions require only a structured filter"
                raise ValueError(msg)
        elif subject_id is None or record_filter is not None:
            msg = "creator and record subscriptions require only a subject_id"
            raise ValueError(msg)
        elif not await self._repository.subscription_target_exists(kind, subject_id):
            raise NotificationSubscriptionNotFoundError(public_context={"subject_id": str(subject_id)})
        return await self._repository.add_subscription(
            account_id,
            kind=kind,
            subject_id=subject_id,
            record_filter=record_filter,
        )

    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        """List the caller's enabled subscriptions."""
        return await self._repository.list_subscriptions(account_id)

    async def unsubscribe(self, account_id: int, subscription_id: int) -> None:
        """Delete one caller-owned subscription."""
        if not await self._repository.delete_subscription(account_id, subscription_id):
            raise NotificationSubscriptionNotFoundError(public_context={"subscription_id": subscription_id})

    async def inbox(
        self,
        account_id: int,
        *,
        after_id: int | None = None,
        limit: int = 20,
        include_staff: bool = False,
    ) -> Sequence[InboxNotification]:
        """List web-visible inbox items newest first."""
        if not 1 <= limit <= 100:
            msg = "limit must be between 1 and 100"
            raise ValueError(msg)
        return await self._repository.list_inbox(
            account_id,
            after_id=after_id,
            limit=limit,
            include_staff=include_staff,
        )

    async def mark_read(self, account_id: int, notification_id: int, *, include_staff: bool = False) -> None:
        """Mark a visible caller-owned inbox item as read."""
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

    async def can_view_staff(self, discord_id: int) -> bool:
        """Recheck owner or global-administrator access at read and delivery time."""
        return await self._repository.can_view_staff(discord_id)

    async def claim_deliveries(self, *, limit: int = 20) -> Sequence[PendingNotificationDelivery]:
        """Claim ready Discord DMs with database-clock UUID fencing tokens."""
        if not 1 <= limit <= 100:
            msg = "delivery claim limit must be between 1 and 100"
            raise ValueError(msg)
        return await self._repository.claim_deliveries(limit=limit)

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        """Mark a DM sent only while this claim still owns its generation."""
        return await self._repository.complete_delivery(delivery)

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Retry a failed or ambiguous send; duplicates are possible after timeouts."""
        return await self._repository.fail_delivery(delivery, str(error)[:4000], max_attempts=8)

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: Exception) -> bool:
        """Disable DMs after Discord explicitly rejects messages to the recipient."""
        return await self._repository.suspend_dm(delivery, str(error)[:4000])


__all__ = ["CURRENT_NOTIFICATION_NOTICE_VERSION", "NotificationRepository", "NotificationService"]
