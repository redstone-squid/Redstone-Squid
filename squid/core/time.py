"""Shared UTC time formatting."""

from whenever import Instant


def utcnow() -> str:
    """Return the current UTC time formatted as ``YYYY-MM-DDThh:mm:ss``, without a zone suffix."""
    return Instant.now().format("YYYY-MM-DD'T'hh:mm:ss")
