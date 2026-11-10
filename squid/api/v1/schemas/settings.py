"""Runtime settings wire representations. Discord IDs are decimal strings to avoid JavaScript rounding."""

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from squid.settings.domain.runtime import RuntimeSettings


class RuntimeSettingsDetail(BaseModel):
    """Current global defaults and owner-guild routing."""

    model_config = ConfigDict(extra="forbid")

    welcome_forward_chance: float
    welcome_enabled: bool
    redstoner_enabled: bool
    build_vote_pass_threshold: int
    build_vote_fail_threshold: int
    delete_vote_pass_threshold: int
    delete_vote_fail_threshold: int
    notification_retention_days: int
    redstoner_starboard_author_id: str
    redstoner_starboard_channel_id: str
    redstoner_role_id: str
    redstoner_corner_channel_id: str
    redstoner_announcement_channel_id: str
    welcome_channel_id: str
    welcome_relay_channel_id: str
    version_tracker_channel_id: str
    build_log_channel_ids: list[str]

    @classmethod
    def from_domain(cls, settings: RuntimeSettings) -> Self:
        values = settings.model_dump()
        for name in cls.model_fields:
            if name.endswith("_id"):
                values[name] = str(values[name])
            elif name.endswith("_ids"):
                values[name] = [str(value) for value in values[name]]
        return cls.model_validate(values)


class RuntimeSettingsPatch(BaseModel):
    """Only supplied fields change. Null is not a reset operation."""

    model_config = ConfigDict(extra="forbid")

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
