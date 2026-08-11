"""Strict HTTP schemas for CLI device authorization."""

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from squid.cli_auth.application import decode_urlsafe_bytes, public_key_fingerprint
from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, IssuedCliEnrollment, IssuedCliSession

DeviceLabel = Annotated[str, Field(min_length=1, max_length=80)]
PublicKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
DeviceCode = Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
UserCode = Annotated[str, Field(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9-]+$")]
ProofNonce = Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Signature = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]


class StrictSchema(BaseModel):
    """Reject fields outside the pinned CLI authorization contract."""

    model_config = ConfigDict(extra="forbid")


class CliEnrollmentCreateRequest(StrictSchema):
    """Enroll one client-held Ed25519 public key."""

    public_key: PublicKey
    client_instance_id: UUID
    label: DeviceLabel

    def public_key_bytes(self) -> bytes:
        """Decode the exact raw Ed25519 public-key representation."""
        return decode_urlsafe_bytes(self.public_key, expected_length=32)


class CliEnrollmentResponse(StrictSchema):
    """One-time browser approval codes and polling policy."""

    id: UUID
    device_code: str
    user_code: str
    verification_uri: AnyHttpUrl
    verification_uri_complete: AnyHttpUrl
    expires_at: datetime
    polling_interval_seconds: int

    @classmethod
    def from_domain(
        cls,
        issued: IssuedCliEnrollment,
        *,
        verification_uri: AnyHttpUrl,
    ) -> "CliEnrollmentResponse":
        fragment = urlencode({"code": issued.user_code})
        return cls(
            id=issued.enrollment.id,
            device_code=issued.device_code,
            user_code=issued.user_code,
            verification_uri=verification_uri,
            verification_uri_complete=AnyHttpUrl(f"{verification_uri}#{fragment}"),
            expires_at=issued.enrollment.expires_at.to_stdlib(),
            polling_interval_seconds=issued.polling_interval_seconds,
        )


class CliEnrollmentApprovalRequest(StrictSchema):
    """Approve a displayed CLI user code as the signed-in browser account."""

    user_code: UserCode


class CliEnrollmentApprovalResponse(StrictSchema):
    """Safe device identity shown before and after browser approval."""

    id: UUID
    client_instance_id: UUID
    label: str
    public_key_fingerprint: str
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None

    @classmethod
    def from_domain(cls, enrollment: CliDeviceEnrollment) -> "CliEnrollmentApprovalResponse":
        return cls(
            id=enrollment.id,
            client_instance_id=enrollment.client_instance_id,
            label=enrollment.label,
            public_key_fingerprint=public_key_fingerprint(enrollment.public_key),
            created_at=enrollment.created_at.to_stdlib(),
            expires_at=enrollment.expires_at.to_stdlib(),
            approved_at=None if enrollment.approved_at is None else enrollment.approved_at.to_stdlib(),
        )


class CliEnrollmentExchangeRequest(StrictSchema):
    """Prove the enrolled private key and exchange browser approval."""

    device_code: DeviceCode
    signature: Signature

    def signature_bytes(self) -> bytes:
        """Decode the exact raw Ed25519 signature representation."""
        return decode_urlsafe_bytes(self.signature, expected_length=64)


class CliSessionChallengeRequest(StrictSchema):
    """Request a one-time signing nonce for an enrolled device."""

    device_id: UUID


class CliSessionChallengeResponse(StrictSchema):
    """A one-time plaintext proof nonce."""

    id: UUID
    device_id: UUID
    nonce: str
    expires_at: datetime


class CliSessionExchangeRequest(StrictSchema):
    """Exchange an Ed25519-signed proof nonce for a short session."""

    device_id: UUID
    challenge_id: UUID
    nonce: ProofNonce
    signature: Signature

    def signature_bytes(self) -> bytes:
        """Decode the exact raw Ed25519 signature representation."""
        return decode_urlsafe_bytes(self.signature, expected_length=64)


class CliDeviceResponse(StrictSchema):
    """Account-visible CLI device metadata without its public key."""

    id: UUID
    client_instance_id: UUID
    label: str
    public_key_fingerprint: str
    created_at: datetime
    last_used_at: datetime
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, device: CliDevice) -> "CliDeviceResponse":
        return cls(
            id=device.id,
            client_instance_id=device.client_instance_id,
            label=device.label,
            public_key_fingerprint=public_key_fingerprint(device.public_key),
            created_at=device.created_at.to_stdlib(),
            last_used_at=device.last_used_at.to_stdlib(),
            revoked_at=None if device.revoked_at is None else device.revoked_at.to_stdlib(),
        )


class CliDeviceListResponse(StrictSchema):
    """All CLI devices owned by the signed-in browser account."""

    devices: list[CliDeviceResponse]


class IssuedCliSessionResponse(StrictSchema):
    """One-time CLI bearer token response from a verified device proof."""

    device: CliDeviceResponse
    session_id: UUID
    token: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, issued: IssuedCliSession) -> "IssuedCliSessionResponse":
        return cls(
            device=CliDeviceResponse.from_domain(issued.device),
            session_id=issued.session.id,
            token=issued.token,
            expires_at=issued.session.expires_at.to_stdlib(),
        )
