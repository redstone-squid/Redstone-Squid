"""Domain values for browser-approved CLI devices and sessions."""

from dataclasses import dataclass
from uuid import UUID

from whenever import Instant


@dataclass(frozen=True, slots=True)
class CliDeviceEnrollment:
    """A pending device enrollment containing only code digests."""

    id: UUID
    device_code_hash: bytes
    user_code_hash: bytes
    public_key: bytes
    client_instance_id: UUID
    label: str
    created_at: Instant
    expires_at: Instant
    approved_by_account_id: int | None = None
    approved_at: Instant | None = None
    exchanged_at: Instant | None = None
    revoked_at: Instant | None = None

    def is_expired_at(self, instant: Instant) -> bool:
        """Return whether this enrollment can no longer be used."""
        return self.expires_at <= instant


@dataclass(frozen=True, slots=True)
class IssuedCliEnrollment:
    """Enrollment secrets disclosed only to the initiating CLI."""

    enrollment: CliDeviceEnrollment
    device_code: str
    user_code: str
    polling_interval_seconds: int


@dataclass(frozen=True, slots=True)
class CliDevice:
    """An account-owned Ed25519 device key."""

    id: UUID
    account_id: int
    public_key: bytes
    client_instance_id: UUID
    label: str
    created_at: Instant
    last_used_at: Instant
    revoked_at: Instant | None = None

    def is_active(self) -> bool:
        """Return whether the device remains authorized."""
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class CliSessionChallenge:
    """A short-lived nonce challenge containing only its digest."""

    id: UUID
    device_id: UUID
    nonce_hash: bytes
    created_at: Instant
    expires_at: Instant
    consumed_at: Instant | None = None

    def is_expired_at(self, instant: Instant) -> bool:
        """Return whether this challenge can no longer be consumed."""
        return self.expires_at <= instant


@dataclass(frozen=True, slots=True)
class IssuedCliSessionChallenge:
    """A session challenge and its one-time plaintext nonce."""

    challenge: CliSessionChallenge
    nonce: str


@dataclass(frozen=True, slots=True)
class CliSession:
    """A short-lived CLI bearer session containing only a token digest."""

    id: UUID
    device_id: UUID
    token_hash: bytes
    issued_at: Instant
    expires_at: Instant
    last_seen_at: Instant
    revoked_at: Instant | None = None

    def is_active_at(self, instant: Instant) -> bool:
        """Return whether this session is inside its validity window."""
        return self.revoked_at is None and self.issued_at <= instant < self.expires_at


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
