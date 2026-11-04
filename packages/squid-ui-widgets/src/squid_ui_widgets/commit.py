"""Shared commit timing for machines with staged values."""

from enum import StrEnum


class CommitMode(StrEnum):
    """When a widget accepts its staged edits as committed values.

    EXPLICIT waits for Apply or Save; IMMEDIATE accepts each valid change. This changes
    widget state only. The application's commit callback handles persistent storage.
    """

    EXPLICIT = "explicit"
    IMMEDIATE = "immediate"


__all__ = ["CommitMode"]
