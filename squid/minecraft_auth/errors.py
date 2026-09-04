"""Deterministic errors for Minecraft client authorization."""

from collections.abc import Mapping
from enum import StrEnum

from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    JSONValue,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from squid.core.i18n import tr


class MinecraftAuthReason(StrEnum):
    """Stable Minecraft authorization reasons exposed in problem details."""

    INSTALLATION_UNAVAILABLE = "installation_unavailable"
    ACCOUNT_CONSENT_REQUIRED = "account_consent_required"
    INVALID_INSTALLATION_CREDENTIAL = "invalid_installation_credential"
    INVALID_CHALLENGE = "invalid_challenge"
    AUTHORIZATION_PENDING = "authorization_pending"
    CHALLENGE_EXPIRED = "challenge_expired"
    CHALLENGE_ALREADY_EXCHANGED = "challenge_already_exchanged"
    CHALLENGE_APPROVAL_DENIED = "challenge_approval_denied"
    TOO_MANY_ACTIVE_CHALLENGES = "too_many_active_challenges"
    INVALID_PKCE = "invalid_pkce"
    INVALID_PLAYER_TOKEN = "invalid_player_token"


def _reason_context(
    reason: MinecraftAuthReason,
    context: Mapping[str, JSONValue] | None = None,
) -> dict[str, JSONValue]:
    # Keep the established field name for existing clients while replacing the
    # parallel exception hierarchy with a closed reason vocabulary.
    return {"minecraft_auth_code": reason.value, **(context or {})}


class InstallationUnavailableError(NotFoundError):
    """The requested installation is absent, unowned, revoked, or stale."""

    default_message = tr(t"The Paper installation is unavailable.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.INSTALLATION_UNAVAILABLE))


class AccountConsentRequiredError(AuthorizationError):
    """An account lacks the currently required privacy consent receipt."""

    default_message = tr(t"Current privacy consent is required.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.ACCOUNT_CONSENT_REQUIRED))


class InvalidInstallationCredentialError(AuthenticationError):
    """A Paper installation credential could not be authenticated."""

    default_message = tr(t"The Paper installation credential is invalid.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.INVALID_INSTALLATION_CREDENTIAL))


class InvalidChallengeError(ValidationError):
    """A device or user code does not identify an available challenge."""

    default_message = tr(t"The authorization challenge is invalid.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.INVALID_CHALLENGE))


class AuthorizationPendingError(ConflictError):
    """A valid challenge is still waiting for account approval."""

    default_message = tr(t"The authorization challenge is awaiting approval.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.AUTHORIZATION_PENDING))


class ChallengeExpiredError(ConflictError):
    """A challenge passed its short expiry window."""

    default_message = tr(t"The authorization challenge has expired.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.CHALLENGE_EXPIRED))


class ChallengeAlreadyExchangedError(ConflictError):
    """An approved challenge already yielded its one-time token response."""

    default_message = tr(t"The authorization challenge was already exchanged.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.CHALLENGE_ALREADY_EXCHANGED))


class ChallengeApprovalDeniedError(AuthorizationError):
    """The approving account cannot represent the requested Java identity."""

    default_message = tr(t"This account cannot approve the requested Java identity.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.CHALLENGE_APPROVAL_DENIED))


class TooManyActiveChallengesError(RateLimitedError):
    """An initiator reached its bounded active-challenge allowance."""

    default_message = tr(t"Too many authorization challenges are active.")
    default_resource = "minecraft_auth"
    _RATE_LIMIT_RETRY_SECONDS = 60

    def __init__(self) -> None:
        super().__init__(self._RATE_LIMIT_RETRY_SECONDS)
        self.with_context(public_context=_reason_context(MinecraftAuthReason.TOO_MANY_ACTIVE_CHALLENGES))


class InvalidPkceError(ValidationError):
    """A Fabric PKCE value is malformed or does not match."""

    default_message = tr(t"The Fabric PKCE proof is invalid.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.INVALID_PKCE))


class InvalidPlayerTokenError(AuthenticationError):
    """A player token is malformed, expired, revoked, stale, or mismatched."""

    default_message = tr(t"The Minecraft player token is invalid.")
    default_resource = "minecraft_auth"

    def __init__(self) -> None:
        super().__init__(public_context=_reason_context(MinecraftAuthReason.INVALID_PLAYER_TOKEN))
