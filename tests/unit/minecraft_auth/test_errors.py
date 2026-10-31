import pytest

from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    SquidError,
    ValidationError,
)
from squid.minecraft_auth.errors import (
    AccountConsentRequiredError,
    AuthorizationPendingError,
    ChallengeAlreadyExchangedError,
    ChallengeApprovalDeniedError,
    ChallengeExpiredError,
    InstallationUnavailableError,
    InvalidChallengeError,
    InvalidInstallationCredentialError,
    InvalidPkceError,
    InvalidPlayerTokenError,
    MinecraftAuthReason,
    TooManyActiveChallengesError,
)


@pytest.mark.parametrize(
    ("error_type", "semantic_base", "reason"),
    [
        (InstallationUnavailableError, NotFoundError, MinecraftAuthReason.INSTALLATION_UNAVAILABLE),
        (AccountConsentRequiredError, AuthorizationError, MinecraftAuthReason.ACCOUNT_CONSENT_REQUIRED),
        (
            InvalidInstallationCredentialError,
            AuthenticationError,
            MinecraftAuthReason.INVALID_INSTALLATION_CREDENTIAL,
        ),
        (InvalidChallengeError, ValidationError, MinecraftAuthReason.INVALID_CHALLENGE),
        (AuthorizationPendingError, ConflictError, MinecraftAuthReason.AUTHORIZATION_PENDING),
        (ChallengeExpiredError, ConflictError, MinecraftAuthReason.CHALLENGE_EXPIRED),
        (ChallengeAlreadyExchangedError, ConflictError, MinecraftAuthReason.CHALLENGE_ALREADY_EXCHANGED),
        (ChallengeApprovalDeniedError, AuthorizationError, MinecraftAuthReason.CHALLENGE_APPROVAL_DENIED),
        (TooManyActiveChallengesError, RateLimitedError, MinecraftAuthReason.TOO_MANY_ACTIVE_CHALLENGES),
        (InvalidPkceError, ValidationError, MinecraftAuthReason.INVALID_PKCE),
        (InvalidPlayerTokenError, AuthenticationError, MinecraftAuthReason.INVALID_PLAYER_TOKEN),
    ],
)
def test_minecraft_errors_use_direct_semantic_bases_and_typed_reasons(
    error_type: type[SquidError],
    semantic_base: type[Exception],
    reason: MinecraftAuthReason,
) -> None:
    assert error_type.__bases__ == (semantic_base,)

    error = error_type()
    assert error.resource == "minecraft_auth"
    assert error.public_context["minecraft_auth_code"] == reason.value
