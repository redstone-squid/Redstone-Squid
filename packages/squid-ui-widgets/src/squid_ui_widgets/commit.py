"""Shared commit timing for patterns with staged values."""

from enum import StrEnum


class CommitMode(StrEnum):
    """When a pattern promotes valid staged values to committed values."""

    EXPLICIT = "explicit"
    IMMEDIATE = "immediate"


__all__ = ["CommitMode"]
