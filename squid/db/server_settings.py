"""Shared types for server settings, kept separate to avoid import cycles.

This file should be the only place that is aware of the database column names
for settings.
"""

from typing import Literal, TypedDict

DbSettingKey = Literal[
    "smallest_channel_id",
    "fastest_channel_id",
    "first_channel_id",
    "builds_channel_id",
    "voting_channel_id",
    "staff_roles_ids",
    "trusted_roles_ids",
]


class SettingOptions(TypedDict, total=False):
    """A map of settings to their values."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None
    Staff: list[int]
    Trusted: list[int]
