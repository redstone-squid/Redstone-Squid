"""Notification application errors."""

from squid.core.errors import NotFoundError


class NotificationSubscriptionNotFoundError(NotFoundError):
    """The requested subscription or subscription target is unavailable to the caller."""

    default_message = "Notification subscription not found."
    default_resource = "notification_subscription"


class NotificationNotFoundError(NotFoundError):
    """The requested inbox item is missing or hidden from the caller."""

    default_message = "Notification not found."
    default_resource = "notification"
