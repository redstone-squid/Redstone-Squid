"""Domain values for browser-approved CLI devices and sessions."""

from dataclasses import dataclass
from uuid import UUID

from squid.cli_auth.models import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge


@dataclass(frozen=True, slots=True)
class IssuedCliEnrollment:
    """Enrollment secrets disclosed only to the initiating CLI."""

    enrollment: CliDeviceEnrollment
    device_code: str
    user_code: str
    polling_interval_seconds: int


@dataclass(frozen=True, slots=True)
class IssuedCliSessionChallenge:
    """A session challenge and its one-time plaintext nonce."""

    challenge: CliSessionChallenge
    nonce: str


@dataclass(frozen=True, slots=True)
class IssuedCliSession:
    """A device session and its one-time plaintext bearer token."""

    device: CliDevice
    session: CliSession
    token: str


@dataclass(frozen=True, slots=True)
class CliIdentity:
    """Trusted account and device context produced by CLI session authentication."""

    account_id: int
    device_id: UUID
    session_id: UUID
    consent_pending: bool
