"""Opaque web-session and OAuth state values."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebSessionIdentity:
    """Identity restored from an active opaque session."""

    session_id: str
    account_id: int
    consent_pending: bool
