"""Shared commit timing for machines with staged values."""

from enum import StrEnum


class CommitMode(StrEnum):
    """When a machine promotes valid staged values to committed values."""

    EXPLICIT = "explicit"
    """Only on the machine's Apply/Save action, which renders while something is staged."""
    IMMEDIATE = "immediate"
    """After every transition whose result passes the machine's validation; no commit control renders."""


__all__ = ["CommitMode"]
