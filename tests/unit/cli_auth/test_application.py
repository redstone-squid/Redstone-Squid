"""Tests for browser-approved CLI device authorization."""

import base64
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from whenever import Instant

from squid.cli_auth.application import (
    CliAuthorizationService,
    CliSecretCodec,
    CliSecretPurpose,
    enrollment_proof_message,
    public_key_fingerprint,
    session_proof_message,
)
from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, CliSession, CliSessionChallenge
from squid.cli_auth.errors import InvalidCliDeviceProofError, InvalidCliSessionError

NOW = Instant.parse_iso("2026-08-11T22:00:00Z")
PEPPER = b"test-cli-authorization-pepper-32-bytes"


class FakeConsentReader:
    """Return a configurable current-consent state."""

    def __init__(self, *, current: bool = True) -> None:
        self.current = current

    async def has_current_consent(self, _account_id: int) -> bool:
        return self.current


class FakeCliAuthorizationRepository:
    """Small transaction-free repository for service behavior tests."""

    def __init__(self) -> None:
        self.enrollments: dict[UUID, CliDeviceEnrollment] = {}
        self.devices: dict[UUID, CliDevice] = {}
        self.challenges: dict[UUID, CliSessionChallenge] = {}
        self.sessions: dict[UUID, CliSession] = {}

    async def add_enrollment(self, enrollment: CliDeviceEnrollment, *, max_active: int) -> CliDeviceEnrollment:
        assert max_active > 0
        self.enrollments[enrollment.id] = enrollment
        return enrollment

    async def get_enrollment_by_user_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        return next((item for item in self.enrollments.values() if item.user_code_hash == code_hash), None)

    async def get_enrollment_by_device_code_hash(self, code_hash: bytes) -> CliDeviceEnrollment | None:
        return next((item for item in self.enrollments.values() if item.device_code_hash == code_hash), None)

    async def approve_enrollment(
        self,
        *,
        enrollment_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> CliDeviceEnrollment:
        enrollment = replace(
            self.enrollments[enrollment_id],
            approved_by_account_id=account_id,
            approved_at=approved_at,
        )
        self.enrollments[enrollment_id] = enrollment
        return enrollment

    async def exchange_enrollment(
        self,
        *,
        enrollment_id: UUID,
        device_code_hash: bytes,
        device: CliDevice,
        session: CliSession,
        exchanged_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        assert self.enrollments[enrollment_id].device_code_hash == device_code_hash
        self.enrollments[enrollment_id] = replace(self.enrollments[enrollment_id], exchanged_at=exchanged_at)
        self.devices[device.id] = device
        self.sessions[session.id] = session
        return device, session

    async def add_session_challenge(
        self,
        challenge: CliSessionChallenge,
        *,
        max_active: int,
    ) -> CliSessionChallenge:
        assert max_active > 0
        assert self.devices[challenge.device_id].revoked_at is None
        self.challenges[challenge.id] = challenge
        return challenge

    async def get_device(self, device_id: UUID) -> CliDevice | None:
        return self.devices.get(device_id)

    async def consume_session_challenge(
        self,
        *,
        challenge_id: UUID,
        device_id: UUID,
        nonce_hash: bytes,
        session: CliSession,
        consumed_at: Instant,
    ) -> tuple[CliDevice, CliSession]:
        challenge = self.challenges[challenge_id]
        assert challenge.device_id == device_id
        assert challenge.nonce_hash == nonce_hash
        self.challenges[challenge_id] = replace(challenge, consumed_at=consumed_at)
        self.sessions[session.id] = session
        return self.devices[device_id], session

    async def get_session_with_device(self, session_id: UUID) -> tuple[CliSession, CliDevice] | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        return session, self.devices[session.device_id]

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        return tuple(device for device in self.devices.values() if device.account_id == account_id)

    async def revoke_device(self, *, device_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        device = self.devices.get(device_id)
        if device is None or device.account_id != account_id:
            return False
        self.devices[device_id] = replace(device, revoked_at=revoked_at)
        return True

    async def revoke_session(self, *, session_id: UUID, device_id: UUID, revoked_at: Instant) -> bool:
        session = self.sessions.get(session_id)
        if session is None or session.device_id != device_id:
            return False
        self.sessions[session_id] = replace(session, revoked_at=revoked_at)
        return True


def _key_pair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, public_key


async def _enrolled_service() -> tuple[
    CliAuthorizationService,
    FakeCliAuthorizationRepository,
    Ed25519PrivateKey,
    str,
]:
    repository = FakeCliAuthorizationRepository()
    service = CliAuthorizationService(repository, FakeConsentReader(), CliSecretCodec(PEPPER), now=lambda: NOW)
    private_key, public_key = _key_pair()
    issued = await service.start_enrollment(
        public_key=public_key,
        client_instance_id=uuid4(),
        label="  Workstation   CLI ",
    )
    preview = await service.preview_enrollment(issued.user_code.lower())
    assert preview.label == "Workstation CLI"
    await service.approve_enrollment(user_code=issued.user_code, account_id=42)
    signature = private_key.sign(enrollment_proof_message(issued.enrollment.id, issued.device_code))
    session = await service.exchange_enrollment(device_code=issued.device_code, signature=signature)
    return service, repository, private_key, session.token


async def test_enrollment_proves_private_key_and_issues_authenticated_session() -> None:
    service, repository, _private_key, token = await _enrolled_service()

    identity = await service.authenticate(token)

    assert identity.account_id == 42
    assert identity.consent_pending is False
    assert repository.enrollments
    enrollment = next(iter(repository.enrollments.values()))
    assert len(enrollment.device_code_hash) == 32
    assert len(enrollment.user_code_hash) == 32
    assert token not in repr(repository.sessions)


async def test_enrollment_rejects_signature_from_another_key() -> None:
    repository = FakeCliAuthorizationRepository()
    service = CliAuthorizationService(repository, FakeConsentReader(), CliSecretCodec(PEPPER), now=lambda: NOW)
    _private_key, public_key = _key_pair()
    wrong_key, _wrong_public_key = _key_pair()
    issued = await service.start_enrollment(public_key=public_key, client_instance_id=uuid4(), label="Laptop")
    await service.approve_enrollment(user_code=issued.user_code, account_id=7)

    with pytest.raises(InvalidCliDeviceProofError):
        await service.exchange_enrollment(
            device_code=issued.device_code,
            signature=wrong_key.sign(enrollment_proof_message(issued.enrollment.id, issued.device_code)),
        )


async def test_enrolled_device_can_renew_and_revoke_a_session() -> None:
    service, repository, private_key, first_token = await _enrolled_service()
    identity = await service.authenticate(first_token)
    challenge = await service.start_session_challenge(identity.device_id)
    signature = private_key.sign(session_proof_message(identity.device_id, challenge.challenge.id, challenge.nonce))

    renewed = await service.exchange_session_challenge(
        device_id=identity.device_id,
        challenge_id=challenge.challenge.id,
        nonce=challenge.nonce,
        signature=signature,
    )

    assert (await service.authenticate(renewed.token)).device_id == identity.device_id
    renewed_identity = await service.authenticate(renewed.token)
    assert await service.revoke_current_session(renewed_identity) is True
    with pytest.raises(InvalidCliSessionError):
        await service.authenticate(renewed.token)
    assert repository.challenges[challenge.challenge.id].consumed_at == NOW


def test_codec_and_public_fingerprint_have_stable_wire_shapes() -> None:
    codec = CliSecretCodec(PEPPER)
    session_id = UUID("c016f61a-6bd8-4383-84e9-b77f2905ca86")
    secret = "A" * 43
    token = codec.session_token(session_id, secret)
    _private_key, public_key = _key_pair()

    assert codec.parse_session_token(token) == (session_id, secret)
    assert codec.parse_session_token(f"{token}!") is None
    assert codec.normalize_user_code("abcd efgh") == "ABCDEFGH"
    assert len(codec.digest(CliSecretPurpose.SESSION_TOKEN, secret)) == 32
    assert len(public_key_fingerprint(public_key).split("-")) == 5
    assert len(base64.urlsafe_b64encode(public_key).rstrip(b"=")) == 43
