"""Validated global defaults and owner-guild automation settings.

Each operation reads one immutable snapshot. Changes affect subsequent operations;
existing voting sessions retain their own thresholds.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SettingGroup(StrEnum):
    """The feature a runtime setting configures, so editors can show related settings together."""

    WELCOME = "welcome"
    REDSTONER = "redstoner"
    VOTING = "voting"
    NOTIFICATIONS = "notifications"
    VERSIONS = "versions"
    BUILDS = "builds"


class DiscordKind(StrEnum):
    """What a Discord ID in a setting refers to."""

    CHANNEL = "channel"
    ROLE = "role"
    USER = "user"


_SNOWFLAKE = Field(gt=0, le=9223372036854775807)
ChannelId = Annotated[int, _SNOWFLAKE, DiscordKind.CHANNEL]
RoleId = Annotated[int, _SNOWFLAKE, DiscordKind.ROLE]
UserId = Annotated[int, _SNOWFLAKE, DiscordKind.USER]


class RuntimeSettings(BaseModel):
    """Behavior administrators can change without restarting a process.

    Each field is annotated with its `SettingGroup` and, for Discord IDs, its
    `DiscordKind`; `RUNTIME_FIELDS` reads them back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    welcome_forward_chance: Annotated[float, Field(ge=0, le=1), SettingGroup.WELCOME] = 0.1
    welcome_enabled: Annotated[bool, SettingGroup.WELCOME] = True
    redstoner_enabled: Annotated[bool, SettingGroup.REDSTONER] = True
    build_vote_pass_threshold: Annotated[int, Field(ge=1, le=32767), SettingGroup.VOTING] = 3
    build_vote_fail_threshold: Annotated[int, Field(ge=-32768, le=-1), SettingGroup.VOTING] = -3
    delete_vote_pass_threshold: Annotated[int, Field(ge=1, le=32767), SettingGroup.VOTING] = 3
    delete_vote_fail_threshold: Annotated[int, Field(ge=-32768, le=-1), SettingGroup.VOTING] = -3
    notification_retention_days: Annotated[int, Field(ge=1, le=3650), SettingGroup.NOTIFICATIONS] = 90
    redstoner_starboard_author_id: Annotated[UserId, SettingGroup.REDSTONER] = 700796664276844612
    redstoner_starboard_channel_id: Annotated[ChannelId, SettingGroup.REDSTONER] = 1332630008270684241
    redstoner_role_id: Annotated[RoleId, SettingGroup.REDSTONER] = 433670432420397060
    redstoner_corner_channel_id: Annotated[ChannelId, SettingGroup.REDSTONER] = 534945678850523138
    redstoner_announcement_channel_id: Annotated[ChannelId, SettingGroup.REDSTONER] = 433643026204852224
    welcome_channel_id: Annotated[ChannelId, SettingGroup.WELCOME] = 1356094722531393680
    welcome_relay_channel_id: Annotated[ChannelId, SettingGroup.WELCOME] = 433618741528625155
    version_tracker_channel_id: Annotated[ChannelId, SettingGroup.VERSIONS] = 1334168723170263122
    build_log_channel_ids: Annotated[tuple[ChannelId, ...], SettingGroup.BUILDS, DiscordKind.CHANNEL] = (
        726156829629087814,
        667401499554611210,
    )

    @field_validator("build_log_channel_ids")
    @classmethod
    def _deduplicate_channels(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(dict.fromkeys(value))

    def discord_ids(self, name: str) -> tuple[int, ...]:
        """The Discord IDs one field holds, whether it stores one or many."""
        value = getattr(self, name)
        return value if isinstance(value, tuple) else (value,)


@dataclass(frozen=True, slots=True)
class RuntimeField:
    """How editors should present one runtime setting."""

    group: SettingGroup
    discord_kind: DiscordKind | None = None
    """What the field's IDs refer to, or None when it holds no Discord ID."""


def _runtime_fields() -> Mapping[str, RuntimeField]:
    fields: dict[str, RuntimeField] = {}
    for name, info in RuntimeSettings.model_fields.items():
        group = next(item for item in info.metadata if isinstance(item, SettingGroup))
        kind = next((item for item in info.metadata if isinstance(item, DiscordKind)), None)
        fields[name] = RuntimeField(group, kind)
    return fields


RUNTIME_FIELDS = _runtime_fields()
"""Every runtime setting by field name, in declaration order."""
