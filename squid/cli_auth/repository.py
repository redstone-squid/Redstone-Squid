"""PostgreSQL persistence for CLI device authorization."""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge
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
from squid.cli_auth.models import (
    CliDeviceEnrollmentRecord,
    CliDeviceRecord,
    CliSessionChallengeRecord,
    CliSessionRecord,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _enrollment(record: CliDeviceEnrollmentRecord) -> CliDeviceEnrollment:
    return CliDeviceEnrollment(
        id=record.id,
        device_code_hash=record.device_code_hash,
        user_code_hash=record.user_code_hash,
        public_key=record.public_key,
        client_instance_id=record.client_instance_id,
        label=record.label,
        created_at=record.created_at,
        expires_at=record.expires_at,
        approved_by_account_id=record.approved_by_account_id,
        approved_at=record.approved_at,
        exchanged_at=record.exchanged_at,
        revoked_at=record.revoked_at,
    )


def _device(record: CliDeviceRecord) -> CliDevice:
    return CliDevice(
        id=record.id,
        account_id=record.account_id,
        public_key=record.public_key,
        client_instance_id=record.client_instance_id,
        label=record.label,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
    )


def _challenge(record: CliSessionChallengeRecord) -> CliSessionChallenge:
    return CliSessionChallenge(
        id=record.id,
        device_id=record.device_id,
        nonce_hash=record.nonce_hash,
        created_at=record.created_at,
        expires_at=record.expires_at,
        consumed_at=record.consumed_at,
    )


def _session(record: CliSessionRecord) -> CliSession:
    return CliSession(
        id=record.id,
        device_id=record.device_id,
        token_hash=record.token_hash,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        last_seen_at=record.last_seen_at,
        revoked_at=record.revoked_at,
    )


class PostgresCliAuthorizationRepository:
    """Persist CLI credentials with transactional one-time consumption."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def add_enrollment(self, enrollment: CliDeviceEnrollment, *, max_active: int) -> CliDeviceEnrollment:
        """Insert an enrollment after serializing the per-client active limit."""
        async with self._session_factory.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(_uuid_lock_key(enrollment.client_instance_id))))
            active = await session.scalar(
                select(func.count())
                .select_from(CliDeviceEnrollmentRecord)
                .where(
                    CliDeviceEnrollmentRecord.client_instance_id == enrollment.client_instance_id,
                    CliDeviceEnrollmentRecord.expires_at > enrollment.created_at,
                    CliDeviceEnrollmentRecord.exchanged_at.is_(None),
                    CliDeviceEnrollmentRecord.revoked_at.is_(None),
                )
            )
            if active is not None and active >= max_active:
                raise TooManyActiveCliAuthorizationsError
            record = CliDeviceEnrollmentRecord(
                id=enrollment.id,
                device_code_hash=enrollment.device_code_hash,
                user_code_hash=enrollment.user_code_hash,
                public_key=enrollment.public_key,
                client_instance_id=enrollment.client_instance_id,
                label=enrollment.label,
                created_at=enrollment.created_at,
                expires_at=enrollment.expires_at,
                approved_by_account_id=enrollment.approved_by_account_id,
                approved_at=enrollment.approved_at,
                exchanged_at=enrollment.exchanged_at,
                revoked_at=enrollment.revoked_at,
            )
            session.add(record)
            await session.flush()
            return _enrollment(record)

    async def get_enrollment_by_user_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Resolve a keyed human-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CliDeviceEnrollmentRecord).where(CliDeviceEnrollmentRecord.user_code_hash == code_hash)
            )
            return None if record is None else _enrollment(record)

    async def get_enrollment_by_device_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        """Resolve a keyed device-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CliDeviceEnrollmentRecord).where(CliDeviceEnrollmentRecord.device_code_hash == code_hash)
            )
            return None if record is None else _enrollment(record)

    async def approve_enrollment(
        self,
        *,
        enrollment_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> CliDeviceEnrollment:
        """Approve a live enrollment idempotently for the same account."""
        async with self._session_factory.begin() as session:
            record = await session.get(CliDeviceEnrollmentRecord, enrollment_id, with_for_update=True)
            if record is None or record.revoked_at is not None:
                raise InvalidCliEnrollmentError
            if record.expires_at <= approved_at:
                raise CliEnrollmentExpiredError
            if record.exchanged_at is not None:
                raise CliEnrollmentAlreadyExchangedError
            if record.approved_by_account_id is not None:
                if record.approved_by_account_id != account_id:
                    raise CliEnrollmentApprovalDeniedError
                return _enrollment(record)
            record.approved_by_account_id = account_id
            record.approved_at = approved_at
            await session.flush()
            return _enrollment(record)

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
        async with self._session_factory.begin() as db:
            record = await db.get(CliDeviceEnrollmentRecord, enrollment_id, with_for_update=True)
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
                select(CliDeviceRecord).where(CliDeviceRecord.public_key == record.public_key).with_for_update()
            )
            if device_record is None:
                device_record = CliDeviceRecord(
                    id=device.id,
                    account_id=device.account_id,
                    public_key=device.public_key,
                    client_instance_id=device.client_instance_id,
                    label=device.label,
                    created_at=device.created_at,
                    last_used_at=exchanged_at,
                    revoked_at=device.revoked_at,
                )
                db.add(device_record)
                await db.flush()
            elif device_record.account_id != device.account_id or device_record.revoked_at is not None:
                raise CliDeviceUnavailableError
            else:
                device_record.client_instance_id = device.client_instance_id
                device_record.label = device.label
                device_record.last_used_at = exchanged_at

            session_record = CliSessionRecord(
                id=session.id,
                device_id=device_record.id,
                token_hash=session.token_hash,
                issued_at=session.issued_at,
                expires_at=session.expires_at,
                last_seen_at=session.last_seen_at,
                revoked_at=session.revoked_at,
            )
            record.exchanged_at = exchanged_at
            db.add(session_record)
            await db.flush()
            return _device(device_record), _session(session_record)

    async def add_session_challenge(
        self,
        challenge: CliSessionChallenge,
        *,
        max_active: int,
    ) -> CliSessionChallenge:
        """Issue a nonce only for an active device and within its outstanding limit."""
        async with self._session_factory.begin() as session:
            device = await session.get(CliDeviceRecord, challenge.device_id, with_for_update=True)
            if device is None or device.revoked_at is not None:
                raise CliDeviceUnavailableError
            active = await session.scalar(
                select(func.count())
                .select_from(CliSessionChallengeRecord)
                .where(
                    CliSessionChallengeRecord.device_id == challenge.device_id,
                    CliSessionChallengeRecord.expires_at > challenge.created_at,
                    CliSessionChallengeRecord.consumed_at.is_(None),
                )
            )
            if active is not None and active >= max_active:
                raise TooManyActiveCliAuthorizationsError
            record = CliSessionChallengeRecord(
                id=challenge.id,
                device_id=challenge.device_id,
                nonce_hash=challenge.nonce_hash,
                created_at=challenge.created_at,
                expires_at=challenge.expires_at,
                consumed_at=challenge.consumed_at,
            )
            session.add(record)
            await session.flush()
            return _challenge(record)

    async def get_device(self, device_id: UUID) -> CliDevice | None:
        """Return one device without sessions or private account data."""
        async with self._session_factory() as session:
            record = await session.get(CliDeviceRecord, device_id)
            return None if record is None else _device(record)

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
        async with self._session_factory.begin() as db:
            challenge = await db.get(CliSessionChallengeRecord, challenge_id, with_for_update=True)
            if (
                challenge is None
                or challenge.device_id != device_id
                or challenge.consumed_at is not None
                or not hmac.compare_digest(challenge.nonce_hash, nonce_hash)
            ):
                raise InvalidCliSessionChallengeError
            if challenge.expires_at <= consumed_at:
                raise CliSessionChallengeExpiredError
            device = await db.get(CliDeviceRecord, device_id, with_for_update=True)
            if device is None or device.revoked_at is not None or session.device_id != device.id:
                raise CliDeviceUnavailableError
            challenge.consumed_at = consumed_at
            device.last_used_at = consumed_at
            record = CliSessionRecord(
                id=session.id,
                device_id=session.device_id,
                token_hash=session.token_hash,
                issued_at=session.issued_at,
                expires_at=session.expires_at,
                last_seen_at=session.last_seen_at,
                revoked_at=session.revoked_at,
            )
            db.add(record)
            await db.flush()
            return _device(device), _session(record)

    async def get_session_with_device(self, session_id: UUID) -> tuple[CliSession, CliDevice] | None:
        """Load one session and its active-state authority source."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CliSessionRecord, CliDeviceRecord)
                    .join(CliDeviceRecord, CliDeviceRecord.id == CliSessionRecord.device_id)
                    .where(CliSessionRecord.id == session_id)
                )
            ).one_or_none()
            if row is None:
                return None
            return _session(row[0]), _device(row[1])

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        """List all active and revoked devices for account self-service."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(CliDeviceRecord)
                    .where(CliDeviceRecord.account_id == account_id)
                    .order_by(CliDeviceRecord.created_at.desc(), CliDeviceRecord.id)
                )
            ).all()
            return tuple(_device(record) for record in records)

    async def revoke_device(self, *, device_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke an account-owned device and every still-active session."""
        async with self._session_factory.begin() as session:
            device = await session.scalar(
                select(CliDeviceRecord)
                .where(CliDeviceRecord.id == device_id, CliDeviceRecord.account_id == account_id)
                .with_for_update()
            )
            if device is None:
                return False
            if device.revoked_at is None:
                device.revoked_at = revoked_at
                await session.execute(
                    update(CliSessionRecord)
                    .where(CliSessionRecord.device_id == device_id, CliSessionRecord.revoked_at.is_(None))
                    .values(revoked_at=revoked_at)
                )
                await session.flush()
            return True

    async def revoke_session(self, *, session_id: UUID, device_id: UUID, revoked_at: Instant) -> bool:
        """Revoke one exact device session idempotently."""
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(CliSessionRecord)
                .where(CliSessionRecord.id == session_id, CliSessionRecord.device_id == device_id)
                .with_for_update()
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
