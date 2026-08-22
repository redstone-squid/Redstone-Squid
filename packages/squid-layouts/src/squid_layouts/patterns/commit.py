"""Shared commit timing for patterns with staged values."""

from enum import StrEnum


class CommitPolicy(StrEnum):
    """When a pattern promotes valid staged values to committed values."""

    EXPLICIT = "explicit"
    IMMEDIATE = "immediate"


__all__ = ["CommitPolicy"]
