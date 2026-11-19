"""Persisted submission records."""

from enum import StrEnum


class IntakeStatus(StrEnum):
    """State of a supplied file before and after durable processor registration."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DISCARDED = "discarded"
