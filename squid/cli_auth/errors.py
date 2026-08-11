"""Expected failures from CLI device authorization."""


class CliAuthorizationError(RuntimeError):
    """Base class for expected CLI authorization failures."""

    code = "cli_authorization_error"


class InvalidCliEnrollmentError(CliAuthorizationError):
    """An enrollment code is malformed, unknown, or revoked."""

    code = "invalid_cli_enrollment"

    def __init__(self) -> None:
        super().__init__("The CLI enrollment is invalid.")


class CliEnrollmentExpiredError(CliAuthorizationError):
    """An enrollment expired before approval or exchange."""

    code = "cli_enrollment_expired"

    def __init__(self) -> None:
        super().__init__("The CLI enrollment has expired.")


class CliAuthorizationPendingError(CliAuthorizationError):
    """An enrollment has not yet been approved in the browser."""

    code = "cli_authorization_pending"

    def __init__(self) -> None:
        super().__init__("The CLI enrollment is awaiting browser approval.")


class CliEnrollmentAlreadyExchangedError(CliAuthorizationError):
    """An enrollment has already been exchanged."""

    code = "cli_enrollment_already_exchanged"

    def __init__(self) -> None:
        super().__init__("The CLI enrollment was already exchanged.")


class CliEnrollmentApprovalDeniedError(CliAuthorizationError):
    """An enrollment was already approved by another account."""

    code = "cli_enrollment_approval_denied"

    def __init__(self) -> None:
        super().__init__("This account cannot approve the CLI enrollment.")


class InvalidCliDeviceProofError(CliAuthorizationError):
    """An Ed25519 device proof is malformed or invalid."""

    code = "invalid_cli_device_proof"

    def __init__(self) -> None:
        super().__init__("The CLI device proof is invalid.")


class InvalidCliSessionChallengeError(CliAuthorizationError):
    """A session challenge is malformed, unknown, or unavailable."""

    code = "invalid_cli_session_challenge"

    def __init__(self) -> None:
        super().__init__("The CLI session challenge is invalid.")


class CliSessionChallengeExpiredError(CliAuthorizationError):
    """A session challenge expired before it was consumed."""

    code = "cli_session_challenge_expired"

    def __init__(self) -> None:
        super().__init__("The CLI session challenge has expired.")


class InvalidCliSessionError(CliAuthorizationError):
    """A CLI bearer session is malformed, expired, or revoked."""

    code = "invalid_cli_session"

    def __init__(self) -> None:
        super().__init__("The CLI session is invalid.")


class CliDeviceUnavailableError(CliAuthorizationError):
    """A CLI device is unknown, revoked, or owned by another account."""

    code = "cli_device_unavailable"

    def __init__(self) -> None:
        super().__init__("The CLI device is unavailable.")


class TooManyActiveCliAuthorizationsError(CliAuthorizationError):
    """A client or device has too many outstanding authorization requests."""

    code = "too_many_active_cli_authorizations"

    def __init__(self) -> None:
        super().__init__("Too many CLI authorization requests are active.")
