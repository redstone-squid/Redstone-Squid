"""Expected failures from CLI device authorization."""


class CliAuthorizationError(RuntimeError):
    """Base class for expected CLI authorization failures."""


class InvalidCliEnrollmentError(CliAuthorizationError):
    """An enrollment code is malformed, unknown, or revoked."""


class CliEnrollmentExpiredError(CliAuthorizationError):
    """An enrollment expired before approval or exchange."""


class CliAuthorizationPendingError(CliAuthorizationError):
    """An enrollment has not yet been approved in the browser."""


class CliEnrollmentAlreadyExchangedError(CliAuthorizationError):
    """An enrollment has already been exchanged."""


class CliEnrollmentApprovalDeniedError(CliAuthorizationError):
    """An enrollment was already approved by another account."""


class InvalidCliDeviceProofError(CliAuthorizationError):
    """An Ed25519 device proof is malformed or invalid."""


class InvalidCliSessionChallengeError(CliAuthorizationError):
    """A session challenge is malformed, unknown, or unavailable."""


class CliSessionChallengeExpiredError(CliAuthorizationError):
    """A session challenge expired before it was consumed."""


class InvalidCliSessionError(CliAuthorizationError):
    """A CLI bearer session is malformed, expired, or revoked."""


class CliDeviceUnavailableError(CliAuthorizationError):
    """A CLI device is unknown, revoked, or owned by another account."""


class TooManyActiveCliAuthorizationsError(CliAuthorizationError):
    """A client or device has too many outstanding authorization requests."""
