"""Deterministic errors for Minecraft client authorization."""


class MinecraftAuthorizationError(RuntimeError):
    """Base error carrying a stable machine-readable code."""

    code = "minecraft_authorization_error"


class InstallationUnavailableError(MinecraftAuthorizationError):
    """The requested installation is absent, unowned, revoked, or stale."""

    code = "installation_unavailable"

    def __init__(self) -> None:
        super().__init__("The Paper installation is unavailable.")


class AccountConsentRequiredError(MinecraftAuthorizationError):
    """An account lacks the currently required privacy consent receipt."""

    code = "account_consent_required"

    def __init__(self) -> None:
        super().__init__("Current privacy consent is required.")


class InvalidInstallationCredentialError(MinecraftAuthorizationError):
    """A Paper installation credential could not be authenticated."""

    code = "invalid_installation_credential"

    def __init__(self) -> None:
        super().__init__("The Paper installation credential is invalid.")


class InvalidChallengeError(MinecraftAuthorizationError):
    """A device or user code does not identify an available challenge."""

    code = "invalid_challenge"

    def __init__(self) -> None:
        super().__init__("The authorization challenge is invalid.")


class AuthorizationPendingError(MinecraftAuthorizationError):
    """A valid challenge is still waiting for account approval."""

    code = "authorization_pending"

    def __init__(self) -> None:
        super().__init__("The authorization challenge is awaiting approval.")


class ChallengeExpiredError(MinecraftAuthorizationError):
    """A challenge passed its short expiry window."""

    code = "challenge_expired"

    def __init__(self) -> None:
        super().__init__("The authorization challenge has expired.")


class ChallengeAlreadyExchangedError(MinecraftAuthorizationError):
    """An approved challenge already yielded its one-time token response."""

    code = "challenge_already_exchanged"

    def __init__(self) -> None:
        super().__init__("The authorization challenge was already exchanged.")


class ChallengeApprovalDeniedError(MinecraftAuthorizationError):
    """The approving account cannot represent the requested Java identity."""

    code = "challenge_approval_denied"

    def __init__(self) -> None:
        super().__init__("This account cannot approve the requested Java identity.")


class TooManyActiveChallengesError(MinecraftAuthorizationError):
    """An initiator reached its bounded active-challenge allowance."""

    code = "too_many_active_challenges"

    def __init__(self) -> None:
        super().__init__("Too many authorization challenges are active.")


class InvalidPkceError(MinecraftAuthorizationError):
    """A Fabric PKCE value is malformed or does not match."""

    code = "invalid_pkce"

    def __init__(self) -> None:
        super().__init__("The Fabric PKCE proof is invalid.")


class InvalidPlayerTokenError(MinecraftAuthorizationError):
    """A player token is malformed, expired, revoked, stale, or mismatched."""

    code = "invalid_player_token"

    def __init__(self) -> None:
        super().__init__("The Minecraft player token is invalid.")
