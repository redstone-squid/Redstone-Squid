"""Opaque web-session and OAuth state values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class WebSessionIdentity:
    """Identity restored from an active opaque session."""

    session_id: str
    account_id: int
    signed_in_at: Instant
    """When the sign-in that created this session finished; irreversible account changes require it to be recent."""
