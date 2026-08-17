"""Deterministic errors for Minecraft client authorization."""

from collections.abc import Mapping
from typing import ClassVar

from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    JSONValue,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from squid.core.i18n import _


class MinecraftAuthorizationError(DomainError):
    """Base error carrying a stable machine-readable code."""

    code: ClassVar[str] = "minecraft_authorization_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        public_context: Mapping[str, JSONValue] | None = None,
    ) -> None:
        context = {"minecraft_auth_code": type(self).code, **(public_context or {})}
        super().__init__(message, public_context=context)


class InstallationUnavailableError(MinecraftAuthorizationError, NotFoundError):
    """The requested installation is absent, unowned, revoked, or stale."""

    code = "installation_unavailable"
    default_message = _("The Paper installation is unavailable.")


class AccountConsentRequiredError(MinecraftAuthorizationError, AuthorizationError):
    """An account lacks the currently required privacy consent receipt."""

    code = "account_consent_required"
    default_message = _("Current privacy consent is required.")


class InvalidInstallationCredentialError(MinecraftAuthorizationError, AuthenticationError):
    """A Paper installation credential could not be authenticated."""

    code = "invalid_installation_credential"
    default_message = _("The Paper installation credential is invalid.")


class InvalidChallengeError(MinecraftAuthorizationError, ValidationError):
    """A device or user code does not identify an available challenge."""

    code = "invalid_challenge"
    default_message = _("The authorization challenge is invalid.")


class AuthorizationPendingError(MinecraftAuthorizationError, ConflictError):
    """A valid challenge is still waiting for account approval."""

    code = "authorization_pending"
    default_message = _("The authorization challenge is awaiting approval.")


class ChallengeExpiredError(MinecraftAuthorizationError, ConflictError):
    """A challenge passed its short expiry window."""

    code = "challenge_expired"
    default_message = _("The authorization challenge has expired.")


class ChallengeAlreadyExchangedError(MinecraftAuthorizationError, ConflictError):
    """An approved challenge already yielded its one-time token response."""

    code = "challenge_already_exchanged"
    default_message = _("The authorization challenge was already exchanged.")


class ChallengeApprovalDeniedError(MinecraftAuthorizationError, AuthorizationError):
    """The approving account cannot represent the requested Java identity."""

    code = "challenge_approval_denied"
    default_message = _("This account cannot approve the requested Java identity.")


class TooManyActiveChallengesError(RateLimitedError, MinecraftAuthorizationError):
    """An initiator reached its bounded active-challenge allowance."""

    code = "too_many_active_challenges"
    default_message = _("Too many authorization challenges are active.")
    _RATE_LIMIT_RETRY_SECONDS: ClassVar[int] = 60

    def __init__(self) -> None:
        super().__init__(self._RATE_LIMIT_RETRY_SECONDS)


class InvalidPkceError(MinecraftAuthorizationError, ValidationError):
    """A Fabric PKCE value is malformed or does not match."""

    code = "invalid_pkce"
    default_message = _("The Fabric PKCE proof is invalid.")


class InvalidPlayerTokenError(MinecraftAuthorizationError, AuthenticationError):
    """A player token is malformed, expired, revoked, stale, or mismatched."""

    code = "invalid_player_token"
    default_message = _("The Minecraft player token is invalid.")
