"""Shared UTC time formatting."""

from whenever import Instant


def utcnow() -> str:
    """Return UTC time as YYYY-MM-DDTHH:MM:SS, without a timezone suffix."""
    return Instant.now().format("YYYY-MM-DD'T'hh:mm:ss")
