"""Validated global defaults and owner-guild automation settings.

Each operation reads one immutable snapshot. Changes affect subsequent operations;
existing voting sessions retain their own thresholds.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeSettings(BaseModel):
    """Behavior administrators can change without restarting a process."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    welcome_forward_chance: float = Field(default=0.1, ge=0, le=1)
    welcome_enabled: bool = True
    redstoner_enabled: bool = True
    build_vote_pass_threshold: int = Field(default=3, ge=1, le=32767)
    build_vote_fail_threshold: int = Field(default=-3, ge=-32768, le=-1)
    delete_vote_pass_threshold: int = Field(default=3, ge=1, le=32767)
    delete_vote_fail_threshold: int = Field(default=-3, ge=-32768, le=-1)
    notification_retention_days: int = Field(default=90, ge=1, le=3650)
    redstoner_starboard_author_id: int = Field(default=700796664276844612, gt=0, le=9223372036854775807)
    redstoner_starboard_channel_id: int = Field(default=1332630008270684241, gt=0, le=9223372036854775807)
    redstoner_role_id: int = Field(default=433670432420397060, gt=0, le=9223372036854775807)
    redstoner_corner_channel_id: int = Field(default=534945678850523138, gt=0, le=9223372036854775807)
    redstoner_announcement_channel_id: int = Field(default=433643026204852224, gt=0, le=9223372036854775807)
    welcome_channel_id: int = Field(default=1356094722531393680, gt=0, le=9223372036854775807)
    welcome_relay_channel_id: int = Field(default=433618741528625155, gt=0, le=9223372036854775807)
    version_tracker_channel_id: int = Field(default=1334168723170263122, gt=0, le=9223372036854775807)
    build_log_channel_ids: tuple[int, ...] = (726156829629087814, 667401499554611210)

    @field_validator("build_log_channel_ids")
    @classmethod
    def _validate_channels(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(channel <= 0 or channel > 9223372036854775807 for channel in value):
            msg = "Channel IDs must be positive signed 64-bit integers"
            raise ValueError(msg)
        return tuple(dict.fromkeys(value))
