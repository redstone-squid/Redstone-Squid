"""User notification preferences, subscriptions, inbox, and delivery."""

from squid.notifications.application import NotificationService
from squid.notifications.domain import (
    CURRENT_NOTIFICATION_NOTICE_VERSION,
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)

__all__ = [
    "CURRENT_NOTIFICATION_NOTICE_VERSION",
    "InboxNotification",
    "NotificationPreferences",
    "NotificationService",
    "NotificationSubscription",
    "PendingNotificationDelivery",
    "RecordSubscriptionFilter",
    "SubscriptionKind",
    "TagPredicate",
]
