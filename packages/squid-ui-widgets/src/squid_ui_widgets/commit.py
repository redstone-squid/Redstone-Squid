"""Shared commit timing for machines with staged values."""

from enum import StrEnum


class CommitMode(StrEnum):
    """When a machine promotes valid staged values to committed values."""

    EXPLICIT = "explicit"
    IMMEDIATE = "immediate"


__all__ = ["CommitMode"]
