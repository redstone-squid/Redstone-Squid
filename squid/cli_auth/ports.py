"""Storage and account boundaries for CLI device authorization."""

from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge


class AccountConsentReader(Protocol):
    """Read the current account consent state without coupling to account storage."""

    async def has_current_consent(self, account_id: int) -> bool: ...


class CliAuthorizationRepository(Protocol):
    """Persist enrollment, devices, proof challenges, and bearer sessions."""

    async def add_enrollment(self, enrollment: CliDeviceEnrollment, *, max_active: int) -> CliDeviceEnrollment: ...

    async def get_enrollment_by_user_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None: ...

    async def get_enrollment_by_device_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None: ...

    async def approve_enrollment(
        self,
        *,
        enrollment_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> CliDeviceEnrollment: ...

    async def exchange_enrollment(
        self,
        *,
        enrollment_id: UUID,
        device_code_hash: bytes,
        device: CliDevice,
        session: CliSession,
        exchanged_at: Instant,
    ) -> tuple[CliDevice, CliSession]: ...

    async def add_session_challenge(
        self,
        challenge: CliSessionChallenge,
        *,
        max_active: int,
    ) -> CliSessionChallenge: ...

    async def get_device(self, device_id: UUID) -> CliDevice | None: ...

    async def consume_session_challenge(
        self,
        *,
        challenge_id: UUID,
        device_id: UUID,
        nonce_hash: bytes,
        session: CliSession,
        consumed_at: Instant,
    ) -> tuple[CliDevice, CliSession]: ...

    async def get_session_with_device(self, session_id: UUID) -> tuple[CliSession, CliDevice] | None: ...

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]: ...

    async def revoke_device(self, *, device_id: UUID, account_id: int, revoked_at: Instant) -> bool: ...

    async def revoke_session(self, *, session_id: UUID, device_id: UUID, revoked_at: Instant) -> bool: ...
