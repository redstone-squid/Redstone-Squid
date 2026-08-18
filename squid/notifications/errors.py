"""Notification application errors."""

from squid.core.errors import NotFoundError


class NotificationSubscriptionNotFoundError(NotFoundError):
    """Raised when a caller addresses a subscription they do not own."""

    default_message = "Notification subscription not found."
    default_resource = "notification_subscription"
