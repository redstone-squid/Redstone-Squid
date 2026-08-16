"""Browser-approved Ed25519 device authorization for the standalone CLI."""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from enum import StrEnum
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from whenever import Instant

from squid.cli_auth.domain import (
    CliDevice,
    CliDeviceEnrollment,
    CliIdentity,
    CliSession,
    CliSessionChallenge,
    IssuedCliEnrollment,
    IssuedCliSession,
    IssuedCliSessionChallenge,
)
from squid.cli_auth.errors import (
    CliAuthorizationPendingError,
    CliDeviceUnavailableError,
    CliEnrollmentAlreadyExchangedError,
    CliEnrollmentApprovalDeniedError,
    CliEnrollmentExpiredError,
    InvalidCliDeviceProofError,
    InvalidCliEnrollmentError,
    InvalidCliSessionChallengeError,
    InvalidCliSessionError,
)
from squid.cli_auth.ports import AccountConsentReader, CliAuthorizationRepository

CLI_SESSION_TOKEN_PREFIX = "squid_cli_v1"
ENROLLMENT_LIFETIME_SECONDS = 10 * 60
SESSION_CHALLENGE_LIFETIME_SECONDS = 2 * 60
SESSION_LIFETIME_SECONDS = 15 * 60
POLLING_INTERVAL_SECONDS = 3
MAX_ACTIVE_AUTHORIZATIONS = 5
_USER_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_TOKEN_PATTERN = re.compile(rf"^{CLI_SESSION_TOKEN_PREFIX}_([0-9a-f]{{32}})_([A-Za-z0-9_-]{{32,128}})$")


class CliSecretPurpose(StrEnum):
    """Purpose separation for every keyed CLI digest."""

    DEVICE_CODE = "device-code"
    USER_CODE = "user-code"
    SESSION_NONCE = "session-nonce"
    SESSION_TOKEN = "session-token"


class CliSecretCodec:
    """Generate, parse, and hash one-time CLI authorization secrets."""

    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 32:
            msg = "CLI authorization pepper must contain at least 32 bytes."
            raise ValueError(msg)
        self._pepper = pepper

    @staticmethod
    def random_secret() -> str:
        """Return a high-entropy URL-safe secret without padding."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def random_user_code() -> str:
        """Return a human-readable code without ambiguous characters."""
        value = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
        return f"{value[:4]}-{value[4:]}"

    @staticmethod
    def normalize_user_code(value: str) -> str:
        """Normalize display punctuation and case from a human-entered code."""
        normalized = "".join(character for character in value.upper() if character.isalnum())
        return normalized if len(normalized) == 8 else ""

    def digest(self, purpose: CliSecretPurpose, secret: str) -> bytes:
        """Return a purpose-separated keyed SHA-256 digest."""
        return hmac.digest(self._pepper, f"cli:{purpose.value}\0{secret}".encode(), "sha256")

    @staticmethod
    def session_token(session_id: UUID, secret: str) -> str:
        """Encode the indexed identifier and random bearer secret."""
        return f"{CLI_SESSION_TOKEN_PREFIX}_{session_id.hex}_{secret}"

    @staticmethod
    def parse_session_token(token: str) -> tuple[UUID, str] | None:
        """Parse a bounded CLI bearer token without accessing storage."""
        match = _TOKEN_PATTERN.fullmatch(token)
        if match is None:
            return None
        return UUID(hex=match.group(1)), match.group(2)


class CliAuthorizationService:
    """Enroll, authenticate, list, and revoke account-owned CLI devices."""

    def __init__(
        self,
        repository: CliAuthorizationRepository,
        accounts: AccountConsentReader,
        codec: CliSecretCodec,
        *,
        now: Callable[[], Instant] = Instant.now,
        new_uuid: Callable[[], UUID] = uuid4,
        enrollment_lifetime_seconds: int = ENROLLMENT_LIFETIME_SECONDS,
        session_challenge_lifetime_seconds: int = SESSION_CHALLENGE_LIFETIME_SECONDS,
        session_lifetime_seconds: int = SESSION_LIFETIME_SECONDS,
        max_active_authorizations: int = MAX_ACTIVE_AUTHORIZATIONS,
    ) -> None:
        if (
            min(
                enrollment_lifetime_seconds,
                session_challenge_lifetime_seconds,
                session_lifetime_seconds,
                max_active_authorizations,
            )
            <= 0
        ):
            msg = "CLI authorization limits must be positive."
            raise ValueError(msg)
        self._repository = repository
        self._accounts = accounts
        self._codec = codec
        self._now = now
        self._new_uuid = new_uuid
        self._enrollment_lifetime_seconds = enrollment_lifetime_seconds
        self._session_challenge_lifetime_seconds = session_challenge_lifetime_seconds
        self._session_lifetime_seconds = session_lifetime_seconds
        self._max_active_authorizations = max_active_authorizations

    async def start_enrollment(
        self,
        *,
        public_key: bytes,
        client_instance_id: UUID,
        label: str,
    ) -> IssuedCliEnrollment:
        """Start a browser-approved enrollment for one Ed25519 public key."""
        normalized_label = _device_label(label)
        _validate_public_key(public_key)
        now = self._now()
        device_code = self._codec.random_secret()
        user_code = self._codec.random_user_code()
        enrollment = await self._repository.add_enrollment(
            CliDeviceEnrollment(
                id=self._new_uuid(),
                device_code_hash=self._codec.digest(CliSecretPurpose.DEVICE_CODE, device_code),
                user_code_hash=self._codec.digest(
                    CliSecretPurpose.USER_CODE,
                    self._codec.normalize_user_code(user_code),
                ),
                public_key=public_key,
                client_instance_id=client_instance_id,
                label=normalized_label,
                created_at=now,
                expires_at=now.add(seconds=self._enrollment_lifetime_seconds),
            ),
            max_active=self._max_active_authorizations,
        )
        return IssuedCliEnrollment(enrollment, device_code, user_code, POLLING_INTERVAL_SECONDS)

    async def preview_enrollment(self, user_code: str) -> CliDeviceEnrollment:
        """Return safe device metadata for an authenticated approval screen."""
        enrollment = await self._enrollment_for_user_code(user_code)
        self._ensure_approvable(enrollment, self._now())
        return enrollment

    async def approve_enrollment(self, *, user_code: str, account_id: int) -> CliDeviceEnrollment:
        """Bind one still-live enrollment to the approving browser account."""
        enrollment = await self._enrollment_for_user_code(user_code)
        self._ensure_approvable(enrollment, self._now(), account_id=account_id)
        return await self._repository.approve_enrollment(
            enrollment_id=enrollment.id,
            account_id=account_id,
            approved_at=self._now(),
        )

    async def exchange_enrollment(self, *, device_code: str, signature: bytes) -> IssuedCliSession:
        """Prove the enrolled private key and exchange browser approval for a session."""
        enrollment = await self._enrollment_for_device_code(device_code)
        now = self._now()
        self._ensure_exchangeable(enrollment, now)
        _verify_signature(
            enrollment.public_key,
            signature,
            enrollment_proof_message(enrollment.id, device_code),
        )
        account_id = enrollment.approved_by_account_id
        if account_id is None:
            raise CliAuthorizationPendingError
        device = CliDevice(
            id=self._new_uuid(),
            account_id=account_id,
            public_key=enrollment.public_key,
            client_instance_id=enrollment.client_instance_id,
            label=enrollment.label,
            created_at=now,
            last_used_at=now,
        )
        session, token = self._new_session(device.id, now)
        persisted_device, persisted_session = await self._repository.exchange_enrollment(
            enrollment_id=enrollment.id,
            device_code_hash=self._codec.digest(CliSecretPurpose.DEVICE_CODE, device_code),
            device=device,
            session=session,
            exchanged_at=now,
        )
        if persisted_session.device_id != persisted_device.id:
            msg = "Persisted CLI session does not belong to its device."
            raise ValueError(msg)
        return IssuedCliSession(persisted_device, persisted_session, token)

    async def start_session_challenge(self, device_id: UUID) -> IssuedCliSessionChallenge:
        """Issue a one-time nonce for an enrolled device to sign."""
        now = self._now()
        nonce = self._codec.random_secret()
        challenge = await self._repository.add_session_challenge(
            CliSessionChallenge(
                id=self._new_uuid(),
                device_id=device_id,
                nonce_hash=self._codec.digest(CliSecretPurpose.SESSION_NONCE, nonce),
                created_at=now,
                expires_at=now.add(seconds=self._session_challenge_lifetime_seconds),
            ),
            max_active=self._max_active_authorizations,
        )
        return IssuedCliSessionChallenge(challenge, nonce)

    async def exchange_session_challenge(
        self,
        *,
        device_id: UUID,
        challenge_id: UUID,
        nonce: str,
        signature: bytes,
    ) -> IssuedCliSession:
        """Verify a device proof and consume its nonce into a short-lived session."""
        if not nonce:
            raise InvalidCliSessionChallengeError
        device = await self._repository.get_device(device_id)
        if device is None or not device.is_active():
            raise CliDeviceUnavailableError
        _verify_signature(device.public_key, signature, session_proof_message(device_id, challenge_id, nonce))
        now = self._now()
        session, token = self._new_session(device.id, now)
        persisted_device, persisted_session = await self._repository.consume_session_challenge(
            challenge_id=challenge_id,
            device_id=device_id,
            nonce_hash=self._codec.digest(CliSecretPurpose.SESSION_NONCE, nonce),
            session=session,
            consumed_at=now,
        )
        return IssuedCliSession(persisted_device, persisted_session, token)

    async def authenticate(self, token: str) -> CliIdentity:
        """Authenticate a short-lived CLI bearer token and active device."""
        parsed = self._codec.parse_session_token(token)
        if parsed is None:
            raise InvalidCliSessionError
        session_id, secret = parsed
        stored = await self._repository.get_session_with_device(session_id)
        now = self._now()
        if stored is None:
            raise InvalidCliSessionError
        session, device = stored
        if (
            not session.is_active_at(now)
            or not device.is_active()
            or session.device_id != device.id
            or not hmac.compare_digest(
                session.token_hash,
                self._codec.digest(CliSecretPurpose.SESSION_TOKEN, secret),
            )
        ):
            raise InvalidCliSessionError
        return CliIdentity(
            account_id=device.account_id,
            device_id=device.id,
            session_id=session.id,
            consent_pending=not await self._accounts.has_current_consent(device.account_id),
        )

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        """List devices belonging to one browser-authenticated account."""
        return await self._repository.list_devices(account_id)

    async def revoke_device(self, *, device_id: UUID, account_id: int) -> bool:
        """Revoke an account-owned device and all sessions beneath it."""
        return await self._repository.revoke_device(device_id=device_id, account_id=account_id, revoked_at=self._now())

    async def revoke_current_session(self, identity: CliIdentity) -> bool:
        """Revoke the exact session represented by a CLI principal."""
        return await self._repository.revoke_session(
            session_id=identity.session_id,
            device_id=identity.device_id,
            revoked_at=self._now(),
        )

    async def _enrollment_for_user_code(self, user_code: str) -> CliDeviceEnrollment:
        normalized = self._codec.normalize_user_code(user_code)
        if not normalized:
            raise InvalidCliEnrollmentError
        enrollment = await self._repository.get_enrollment_by_user_code_hash(
            self._codec.digest(CliSecretPurpose.USER_CODE, normalized)
        )
        if enrollment is None:
            raise InvalidCliEnrollmentError
        return enrollment

    async def _enrollment_for_device_code(self, device_code: str) -> CliDeviceEnrollment:
        if not device_code:
            raise InvalidCliEnrollmentError
        enrollment = await self._repository.get_enrollment_by_device_code_hash(
            self._codec.digest(CliSecretPurpose.DEVICE_CODE, device_code)
        )
        if enrollment is None:
            raise InvalidCliEnrollmentError
        return enrollment

    @staticmethod
    def _ensure_approvable(
        enrollment: CliDeviceEnrollment,
        now: Instant,
        *,
        account_id: int | None = None,
    ) -> None:
        if enrollment.revoked_at is not None:
            raise InvalidCliEnrollmentError
        if enrollment.is_expired_at(now):
            raise CliEnrollmentExpiredError
        if enrollment.exchanged_at is not None:
            raise CliEnrollmentAlreadyExchangedError
        if (
            account_id is not None
            and enrollment.approved_by_account_id is not None
            and enrollment.approved_by_account_id != account_id
        ):
            raise CliEnrollmentApprovalDeniedError

    @classmethod
    def _ensure_exchangeable(cls, enrollment: CliDeviceEnrollment, now: Instant) -> None:
        cls._ensure_approvable(enrollment, now)
        if enrollment.approved_by_account_id is None:
            raise CliAuthorizationPendingError

    def _new_session(self, device_id: UUID, now: Instant) -> tuple[CliSession, str]:
        secret = self._codec.random_secret()
        session = CliSession(
            id=self._new_uuid(),
            device_id=device_id,
            token_hash=self._codec.digest(CliSecretPurpose.SESSION_TOKEN, secret),
            issued_at=now,
            expires_at=now.add(seconds=self._session_lifetime_seconds),
            last_seen_at=now,
        )
        return session, self._codec.session_token(session.id, secret)


def enrollment_proof_message(enrollment_id: UUID, device_code: str) -> bytes:
    """Return the versioned message an enrolling CLI must sign."""
    encoded_code = device_code.encode()
    return b"squid-cli-enrollment-v1\0" + enrollment_id.bytes + _length_prefixed(encoded_code)


def session_proof_message(device_id: UUID, challenge_id: UUID, nonce: str) -> bytes:
    """Return the versioned message an enrolled CLI must sign."""
    return b"squid-cli-session-v1\0" + device_id.bytes + challenge_id.bytes + _length_prefixed(nonce.encode())


def public_key_fingerprint(public_key: bytes) -> str:
    """Return a short, stable fingerprint suitable for browser confirmation."""
    _validate_public_key(public_key)
    value = hashlib.sha256(public_key).hexdigest()[:20].upper()
    return "-".join(value[index : index + 4] for index in range(0, len(value), 4))


def decode_urlsafe_bytes(value: str, *, expected_length: int) -> bytes:
    """Decode unpadded URL-safe base64 with an exact decoded length."""
    if not value or len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise InvalidCliDeviceProofError
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise InvalidCliDeviceProofError from error
    if len(decoded) != expected_length:
        raise InvalidCliDeviceProofError
    return decoded


def _verify_signature(public_key: bytes, signature: bytes, message: bytes) -> None:
    if len(signature) != 64:
        raise InvalidCliDeviceProofError
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except InvalidSignature, ValueError:
        raise InvalidCliDeviceProofError from None


def _validate_public_key(public_key: bytes) -> None:
    if len(public_key) != 32:
        raise InvalidCliDeviceProofError
    try:
        Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError:
        raise InvalidCliDeviceProofError from None


def _device_label(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 80:
        msg = "CLI device label must contain 1 to 80 characters."
        raise ValueError(msg)
    return normalized


def _length_prefixed(value: bytes) -> bytes:
    if len(value) > 65535:
        msg = "Signed CLI proof component is too long."
        raise ValueError(msg)
    return len(value).to_bytes(2, byteorder="big") + value
