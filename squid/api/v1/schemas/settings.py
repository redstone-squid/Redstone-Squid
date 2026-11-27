"""Runtime settings wire representations. Discord IDs are decimal strings to avoid JavaScript rounding.

Every property carries `x-squid-group`, and Discord ID properties `x-squid-discord-kind`,
taken from the domain field annotations, so editors can group fields and pick IDs.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

from squid.core.settings.application.discord import ChannelType, DiscordChannel, DiscordRole
from squid.core.settings.domain.runtime import RUNTIME_FIELDS, RuntimeSettings


def _describe_fields(schema: dict[str, Any]) -> None:
    for name, prop in schema.get("properties", {}).items():
        if (field := RUNTIME_FIELDS.get(name)) is None:
            continue
        prop["x-squid-group"] = field.group.value
        if field.discord_kind is not None:
            prop["x-squid-discord-kind"] = field.discord_kind.value


class DiscordReference(BaseModel):
    """A Discord ID with its current name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None
    """Null when Discord no longer knows the ID, the bot cannot see it, or Discord is unavailable."""


class RuntimeSettingsDetail(BaseModel):
    """Current global defaults and owner-guild routing."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_describe_fields)

    revision: int
    names_resolved: bool
    """False when Discord was unavailable, so every name is null."""
    welcome_forward_chance: float
    welcome_enabled: bool
    redstoner_enabled: bool
    build_vote_pass_threshold: int
    build_vote_fail_threshold: int
    delete_vote_pass_threshold: int
    delete_vote_fail_threshold: int
    notification_retention_days: int
    redstoner_starboard_author_id: DiscordReference
    redstoner_starboard_channel_id: DiscordReference
    redstoner_role_id: DiscordReference
    redstoner_corner_channel_id: DiscordReference
    redstoner_announcement_channel_id: DiscordReference
    welcome_channel_id: DiscordReference
    welcome_relay_channel_id: DiscordReference
    version_tracker_channel_id: DiscordReference
    build_log_channel_ids: list[DiscordReference]

    @classmethod
    def from_domain(cls, settings: RuntimeSettings, *, revision: int, names: Mapping[int, str] | None) -> Self:
        known = names or {}

        def reference(discord_id: int) -> DiscordReference:
            return DiscordReference(id=str(discord_id), name=known.get(discord_id))

        values: dict[str, Any] = settings.model_dump() | {"revision": revision, "names_resolved": names is not None}
        for name, field in RUNTIME_FIELDS.items():
            if field.discord_kind is None:
                continue
            value = getattr(settings, name)
            values[name] = [reference(item) for item in value] if isinstance(value, tuple) else reference(value)
        return cls.model_validate(values)


class RuntimeSettingsPatch(BaseModel):
    """Only supplied fields change. Null is not a reset operation."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_describe_fields)

    welcome_forward_chance: float | None = None
    welcome_enabled: bool | None = None
    redstoner_enabled: bool | None = None
    build_vote_pass_threshold: int | None = None
    build_vote_fail_threshold: int | None = None
    delete_vote_pass_threshold: int | None = None
    delete_vote_fail_threshold: int | None = None
    notification_retention_days: int | None = None
    redstoner_starboard_author_id: str | None = None
    redstoner_starboard_channel_id: str | None = None
    redstoner_role_id: str | None = None
    redstoner_corner_channel_id: str | None = None
    redstoner_announcement_channel_id: str | None = None
    welcome_channel_id: str | None = None
    welcome_relay_channel_id: str | None = None
    version_tracker_channel_id: str | None = None
    build_log_channel_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        values = self.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            msg = "Supply at least one setting; values cannot be null."
            raise ValueError(msg)
        # Use the same bounds and field validation as every other settings writer.
        RuntimeSettings.model_validate(RuntimeSettings().model_dump() | values)
        return self


class DiscordChannelDetail(BaseModel):
    """A channel a runtime setting can point at."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: ChannelType
    """Channel settings reject categories, which cannot hold messages."""

    @classmethod
    def listing(cls, channels: Sequence[DiscordChannel]) -> list[Self]:
        return [cls(id=str(channel.id), name=channel.name, type=channel.type) for channel in channels]


class DiscordRoleDetail(BaseModel):
    """A role a runtime setting can point at."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str

    @classmethod
    def listing(cls, roles: Sequence[DiscordRole]) -> list[Self]:
        return [cls(id=str(role.id), name=role.name) for role in roles]
