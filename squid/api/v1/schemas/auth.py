"""Strict browser-session transport schemas."""

from pydantic import BaseModel, ConfigDict, Field


class CsrfTokenResponse(BaseModel):
    """A session-bound double-submit token, never a session credential."""

    model_config = ConfigDict(extra="forbid")

    csrf_token: str = Field(
        min_length=16,
        max_length=128,
        description="Value of the `squid_csrf` cookie. Send it back in the `CSRF-Token` header on every unsafe "
        "method; a request whose header and cookie differ is refused with 403.",
    )
