"""Shared UTC time formatting."""

from whenever import Instant


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    return Instant.now().format("YYYY-MM-DD'T'hh:mm:ss")
