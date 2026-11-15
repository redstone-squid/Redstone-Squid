"""Reasons a Discord message root stops accepting interactions."""

from enum import StrEnum


class CloseReason(StrEnum):
    """The cause accepted with root closure, independent of a screen's result.

    FAILURE means a service failure initiated closure. Cleanup failures are
    reported by wait() and do not replace the original reason.
    """

    CLOSED = "closed"
    TIMEOUT = "timeout"
    PARENT_CLOSED = "parent_closed"
    SCOPE_CLOSED = "scope_closed"
    REPLACED = "replaced"
    SHUTDOWN = "shutdown"
    FAILURE = "failure"
