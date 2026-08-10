"""Opaque web-session and OAuth state values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class OAuthState:
    """One-time PKCE authorization state."""

    state: str
    code_verifier: str
    redirect_to: str | None
    expires_at: Instant


@dataclass(frozen=True, slots=True)
class WebSessionIdentity:
    """Identity restored from an active opaque session."""

    session_id: str
    account_id: int
    discord_id: int
    consent_pending: bool
