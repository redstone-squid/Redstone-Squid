"""Notification application errors."""

from squid.core.errors import NotFoundError


class NotificationSubscriptionNotFoundError(NotFoundError):
    """Raised when a subscription, its target, or an inbox item is not visible to the caller."""

    default_message = "Notification subscription not found."
    default_resource = "notification_subscription"
