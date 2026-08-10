"""Notification application errors."""

from squid.core.errors import ConflictError, NotFoundError


class NotificationConsentRequiredError(ConflictError):
    """Raised when notification state is changed without the current notice."""

    default_message = "Accept the current notification notice before enabling notifications."
    default_resource = "notification_preferences"


class NotificationSubscriptionNotFoundError(NotFoundError):
    """Raised when a caller addresses a subscription they do not own."""

    default_message = "Notification subscription not found."
    default_resource = "notification_subscription"
