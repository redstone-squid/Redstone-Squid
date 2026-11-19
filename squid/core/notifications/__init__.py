"""Account notification preferences, subscriptions, inbox, and delivery."""

from squid.core.notifications.application import NotificationService
from squid.core.notifications.domain import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationCandidate,
    NotificationPreferences,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)

__all__ = [
    "DEFAULT_INBOX_VISIBILITY",
    "InboxVisibility",
    "NotificationCandidate",
    "NotificationPreferences",
    "NotificationService",
    "PendingNotificationDelivery",
    "RecordSubscriptionFilter",
    "SubscriptionKind",
    "TagPredicate",
]
