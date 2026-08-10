"""Strict browser-session transport schemas."""

from pydantic import BaseModel, ConfigDict, Field


class CsrfTokenResponse(BaseModel):
    """A session-bound double-submit token, never a session credential."""

    model_config = ConfigDict(extra="forbid")

    csrf_token: str = Field(min_length=16, max_length=128)
