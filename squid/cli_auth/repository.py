"""PostgreSQL persistence for CLI device authorization."""

import hashlib
import hmac
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.cli_auth.errors import (
    CliAuthorizationPendingError,
    CliDeviceUnavailableError,
    CliEnrollmentAlreadyExchangedError,
    CliEnrollmentApprovalDeniedError,
    CliEnrollmentExpiredError,
    CliSessionChallengeExpiredError,
    InvalidCliEnrollmentError,
    InvalidCliSessionChallengeError,
    TooManyActiveCliAuthorizationsError,
)
from squid.cli_auth.models import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge


class PostgresCliAuthorizationRepository:
    """Persist CLI credentials with transactional one-time consumption."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def add_enrollment(self, enrollment: CliDeviceEnrollment, *, max_active: int) -> CliDeviceEnrollment:
        """Insert an enrollment after serializing the per-client active limit."""
        session = self._session()
        await session.execute(select(func.pg_advisory_xact_lock(_uuid_lock_key(enrollment.client_instance_id))))
        active = await session.scalar(
            select(func.count())
            .select_from(CliDeviceEnrollment)
            .where(
                CliDeviceEnrollment.client_instance_id == enrollment.client_instance_id,
                CliDeviceEnrollment.expires_at > enrollment.created_at,
                CliDeviceEnrollment.exchanged_at.is_(None),
                CliDeviceEnrollment.revoked_at.is_(None),
            )
        )
        if active is not None and active >= max_active:
            raise TooManyActiveCliAuthorizationsError
        record = enrollment
        session.add(record)
        await session.flush()
        return record

    async def get_enrollment_by_user_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Resolve a keyed human-code digest."""
        session = self._session()
        return await session.scalar(select(CliDeviceEnrollment).where(CliDeviceEnrollment.user_code_hash == code_hash))

    async def get_enrollment_by_device_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Resolve a keyed device-code digest."""
        session = self._session()
        return await session.scalar(
            select(CliDeviceEnrollment).where(CliDeviceEnrollment.device_code_hash == code_hash)
        )

    async def approve_enrollment(
        self,
        *,
        enrollment_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> CliDeviceEnrollment:
        """Approve a live enrollment idempotently for the same account."""
        session = self._session()
        record = await session.get(CliDeviceEnrollment, enrollment_id, with_for_update=True)
        if record is None or record.revoked_at is not None:
            raise InvalidCliEnrollmentError
        if record.expires_at <= approved_at:
            raise CliEnrollmentExpiredError
        if record.exchanged_at is not None:
            raise CliEnrollmentAlreadyExchangedError
        if record.approved_by_account_id is not None:
            if record.approved_by_account_id != account_id:
                raise CliEnrollmentApprovalDeniedError
            return record
        record.approved_by_account_id = account_id
        record.approved_at = approved_at
        await session.flush()
        return record

    async def exchange_enrollment(
        self,
        *,
        enrollment_id: UUID,
        device_code_hash: bytes,
        device: CliDevice,
        session: CliSession,
        exchanged_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        """Consume browser approval and create its device session atomically."""
        db = self._session()
        record = await db.get(CliDeviceEnrollment, enrollment_id, with_for_update=True)
        if (
            record is None
            or record.revoked_at is not None
            or not hmac.compare_digest(record.device_code_hash, device_code_hash)
        ):
            raise InvalidCliEnrollmentError
        if record.expires_at <= exchanged_at:
            raise CliEnrollmentExpiredError
        if record.exchanged_at is not None:
            raise CliEnrollmentAlreadyExchangedError
        if record.approved_by_account_id is None:
            raise CliAuthorizationPendingError
        if record.approved_by_account_id != device.account_id or record.public_key != device.public_key:
            raise InvalidCliEnrollmentError

        await db.execute(select(func.pg_advisory_xact_lock(_bytes_lock_key(record.public_key))))
        device_record = await db.scalar(
            select(CliDevice).where(CliDevice.public_key == record.public_key).with_for_update()
        )
        if device_record is None:
            device_record = device
            device_record.last_used_at = exchanged_at
            db.add(device_record)
            await db.flush()
        elif device_record.account_id != device.account_id or device_record.revoked_at is not None:
            raise CliDeviceUnavailableError
        else:
            device_record.client_instance_id = device.client_instance_id
            device_record.label = device.label
            device_record.last_used_at = exchanged_at

        session_record = session
        session_record.device_id = device_record.id
        record.exchanged_at = exchanged_at
        db.add(session_record)
        await db.flush()
        return device_record, session_record

    async def add_session_challenge(
        self,
        challenge: CliSessionChallenge,
        *,
        max_active: int,
    ) -> CliSessionChallenge:
        """Issue a nonce only for an active device and within its outstanding limit."""
        session = self._session()
        device = await session.get(CliDevice, challenge.device_id, with_for_update=True)
        if device is None or device.revoked_at is not None:
            raise CliDeviceUnavailableError
        active = await session.scalar(
            select(func.count())
            .select_from(CliSessionChallenge)
            .where(
                CliSessionChallenge.device_id == challenge.device_id,
                CliSessionChallenge.expires_at > challenge.created_at,
                CliSessionChallenge.consumed_at.is_(None),
            )
        )
        if active is not None and active >= max_active:
            raise TooManyActiveCliAuthorizationsError
        record = challenge
        session.add(record)
        await session.flush()
        return record

    async def get_device(self, device_id: UUID) -> CliDevice | None:
        """Return one device without sessions or private account data."""
        session = self._session()
        return await session.get(CliDevice, device_id)

    async def consume_session_challenge(
        self,
        *,
        challenge_id: UUID,
        device_id: UUID,
        nonce_hash: bytes,
        session: CliSession,
        consumed_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        """Consume a matching nonce and create a bearer session atomically."""
        db = self._session()
        challenge = await db.get(CliSessionChallenge, challenge_id, with_for_update=True)
        if (
            challenge is None
            or challenge.device_id != device_id
            or challenge.consumed_at is not None
            or not hmac.compare_digest(challenge.nonce_hash, nonce_hash)
        ):
            raise InvalidCliSessionChallengeError
        if challenge.expires_at <= consumed_at:
            raise CliSessionChallengeExpiredError
        device = await db.get(CliDevice, device_id, with_for_update=True)
        if device is None or device.revoked_at is not None or session.device_id != device.id:
            raise CliDeviceUnavailableError
        challenge.consumed_at = consumed_at
        device.last_used_at = consumed_at
        record = session
        db.add(record)
        await db.flush()
        return device, record

    async def get_session_with_device(self, session_id: UUID) -> tuple[CliSession, CliDevice] | None:
        """Load one session and its active-state authority source."""
        session = self._session()
        row = (
            await session.execute(
                select(CliSession, CliDevice)
                .join(CliDevice, CliDevice.id == CliSession.device_id)
                .where(CliSession.id == session_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        """List all active and revoked devices for account self-service."""
        session = self._session()
        records = (
            await session.scalars(
                select(CliDevice)
                .where(CliDevice.account_id == account_id)
                .order_by(CliDevice.created_at.desc(), CliDevice.id)
            )
        ).all()
        return tuple(record for record in records)

    async def revoke_device(self, *, device_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke an account-owned device and every still-active session."""
        session = self._session()
        device = await session.scalar(
            select(CliDevice).where(CliDevice.id == device_id, CliDevice.account_id == account_id).with_for_update()
        )
        if device is None:
            return False
        if device.revoked_at is None:
            device.revoked_at = revoked_at
            await session.execute(
                update(CliSession)
                .where(CliSession.device_id == device_id, CliSession.revoked_at.is_(None))
                .values(revoked_at=revoked_at)
            )
            await session.flush()
        return True

    async def revoke_session(self, *, session_id: UUID, device_id: UUID, revoked_at: Instant) -> bool:
        """Revoke one exact device session idempotently."""
        session = self._session()
        record = await session.scalar(
            select(CliSession).where(CliSession.id == session_id, CliSession.device_id == device_id).with_for_update()
        )
        if record is None:
            return False
        if record.revoked_at is None:
            record.revoked_at = revoked_at
            await session.flush()
        return True


def _uuid_lock_key(value: UUID) -> int:
    return _bytes_lock_key(value.bytes)


def _bytes_lock_key(value: bytes) -> int:
    return int.from_bytes(hashlib.sha256(value).digest()[:8], byteorder="big", signed=True)
