"""Storage and account boundaries for CLI device authorization."""

from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge


class AccountConsentReader(Protocol):
    """Read the current account consent state without coupling to account storage."""

    async def has_current_consent(self, account_id: int) -> bool:
        """Return whether the account has accepted the current consent version."""
        ...


class CliAuthorizationRepository(Protocol):
    """Persist enrollment, devices, proof challenges, and bearer sessions."""

    async def add_enrollment(self, enrollment: CliDeviceEnrollment, *, max_active: int) -> CliDeviceEnrollment:
        """Insert an enrollment, raising `TooManyActiveCliAuthorizationsError` past *max_active*.

        The count of live enrollments for the client instance and the insert are serialized, so
        concurrent starts cannot both pass the limit.
        """
        ...

    async def get_enrollment_by_user_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Return the enrollment holding this human-code digest, expired or revoked ones included."""
        ...

    async def get_enrollment_by_device_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Return the enrollment holding this device-code digest, expired or revoked ones included."""
        ...

    async def approve_enrollment(
        self,
        *,
        enrollment_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> CliDeviceEnrollment:
        """Record browser approval, idempotently for the account that already approved it.

        Raises:
            InvalidCliEnrollmentError: If the enrollment is unknown or revoked.
            CliEnrollmentExpiredError: If it expired before *approved_at*.
            CliEnrollmentAlreadyExchangedError: If it was already exchanged for a device.
            CliEnrollmentApprovalDeniedError: If another account approved it first.
        """
        ...

    async def exchange_enrollment(
        self,
        *,
        enrollment_id: UUID,
        device_code_hash: bytes,
        device: CliDevice,
        session: CliSession,
        exchanged_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        """Spend an approved enrollment for a device and its first session, in one transaction.

        A public key already enrolled to the same account is reused and re-labelled rather than
        duplicated, so *device*'s identifiers are proposals.

        Raises:
            InvalidCliEnrollmentError: If the enrollment is unknown, revoked, or does not match the
                device code, account or public key presented.
            CliEnrollmentExpiredError: If it expired before *exchanged_at*.
            CliEnrollmentAlreadyExchangedError: If it was already spent.
            CliAuthorizationPendingError: If nobody has approved it yet.
            CliDeviceUnavailableError: If the public key belongs to another account or a revoked
                device.
        """
        ...

    async def add_session_challenge(
        self,
        challenge: CliSessionChallenge,
        *,
        max_active: int,
    ) -> CliSessionChallenge:
        """Store a nonce challenge for an active device.

        Raises `CliDeviceUnavailableError` for an unknown or revoked device, and
        `TooManyActiveCliAuthorizationsError` past *max_active* unconsumed live challenges.
        """
        ...

    async def get_device(self, device_id: UUID) -> CliDevice | None:
        """Return one device, revoked ones included."""
        ...

    async def consume_session_challenge(
        self,
        *,
        challenge_id: UUID,
        device_id: UUID,
        nonce_hash: bytes,
        session: CliSession,
        consumed_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        """Spend a challenge for a bearer session, in one transaction, and stamp the device as used.

        Raises:
            InvalidCliSessionChallengeError: If the challenge is unknown, already consumed, or does
                not match this device and nonce.
            CliSessionChallengeExpiredError: If it expired before *consumed_at*.
            CliDeviceUnavailableError: If the device is unknown or revoked.
        """
        ...

    async def get_session_with_device(self, session_id: UUID) -> tuple[CliSession, CliDevice] | None:
        """Return a session with the device whose revocation also ends it."""
        ...

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        """List an account's devices, revoked ones included, newest first."""
        ...

    async def revoke_device(self, *, device_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke an account-owned device and its live sessions; return whether the device exists.

        Idempotent: an already revoked device keeps its original instant and still returns True.
        """
        ...

    async def revoke_session(self, *, session_id: UUID, device_id: UUID, revoked_at: Instant) -> bool:
        """Revoke one session of one device; return whether that pair exists.

        Idempotent: an already revoked session keeps its original instant and still returns True.
        """
        ...
