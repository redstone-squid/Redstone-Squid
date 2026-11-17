"""Immutable value validation shared by scene declarations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from squid_ui.scene.base import SceneNode


class SceneValue(BaseModel, SceneNode):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("instant", check_fields=False)
    @classmethod
    def _aware_instant(cls, value: str) -> str:
        # Keep the instant's source text for renderers while checking the wire format.
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            message = "instant must be an ISO-8601 date-time"
            raise ValueError(message) from error
        if parsed.utcoffset() is None:
            message = "instant must include a UTC offset"
            raise ValueError(message)
        return value
