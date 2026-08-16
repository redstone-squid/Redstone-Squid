"""Opaque web-session and OAuth state values."""

from dataclasses import dataclass

from whenever import Instant

from squid.accounts.domain import IdentityProvider


@dataclass(frozen=True, slots=True)
class OAuthState:
    """One-time PKCE authorization state.

    Carries the provider it was minted for so a callback can refuse a state issued for a
    different one. Without that, a state minted at provider A is redeemable at provider
    B's callback, which is the IdP mix-up class.
    """

    state: str
    code_verifier: str
    redirect_to: str | None
    expires_at: Instant
    provider: IdentityProvider = IdentityProvider.DISCORD


@dataclass(frozen=True, slots=True)
class WebSessionIdentity:
    """Identity restored from an active opaque session."""

    session_id: str
    account_id: int
    consent_pending: bool
