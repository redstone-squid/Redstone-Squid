"""Strict HTTP schemas for CLI device authorization."""

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from pydantic import AnyHttpUrl, ConfigDict, Field

from squid.api.schema import ApiSchema
from squid.cli_auth.application import decode_urlsafe_bytes, public_key_fingerprint
from squid.cli_auth.domain import CliDevice, CliDeviceEnrollment, IssuedCliEnrollment, IssuedCliSession

DeviceLabel = Annotated[str, Field(min_length=1, max_length=80, description="Free text shown to the account owner.")]
PublicKey = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9_-]{43}$", description="A raw 32-byte Ed25519 public key, unpadded URL-safe base64."),
]
DeviceCode = Annotated[
    str,
    Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The `device_code` returned by enrollment, echoed verbatim.",
    ),
]
UserCode = Annotated[
    str,
    Field(
        min_length=8,
        max_length=32,
        pattern=r"^[A-Za-z0-9-]+$",
        description="The code displayed by the CLI. Case and dashes are ignored, but it must normalize to exactly "
        "eight alphanumerics.",
    ),
]
ProofNonce = Annotated[
    str,
    Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The `nonce` returned by the session challenge, echoed verbatim.",
    ),
]
Signature = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9_-]{86}$", description="A raw 64-byte Ed25519 signature, unpadded URL-safe base64."),
]


class StrictSchema(ApiSchema):
    """Reject fields outside the pinned CLI authorization contract."""

    model_config = ConfigDict(extra="forbid")


class CliEnrollmentCreateRequest(StrictSchema):
    """Enroll one client-held Ed25519 public key."""

    public_key: PublicKey
    client_instance_id: UUID
    label: DeviceLabel

    def public_key_bytes(self) -> bytes:
        """Decode the 32-byte Ed25519 key, raising `InvalidCliDeviceProofError` at any other length."""
        return decode_urlsafe_bytes(self.public_key, expected_length=32)


class CliEnrollmentResponse(StrictSchema):
    """One-time browser approval codes and polling policy."""

    id: UUID
    device_code: str = Field(
        description="Secret half of the enrollment, disclosed once. Signed at exchange, and never displayed."
    )
    user_code: str = Field(
        description="Code the user types into the browser, formatted `XXXX-XXXX` over an alphabet with no ambiguous "
        "characters."
    )
    verification_uri: AnyHttpUrl = Field(description="Browser page where the user enters `user_code`.")
    verification_uri_complete: AnyHttpUrl = Field(
        description="Same page with `user_code` in the fragment, so a follow can skip typing it."
    )
    expires_at: datetime = Field(
        description="When the approval window closes; the enrollment cannot be approved or exchanged afterwards."
    )
    polling_interval_seconds: int = Field(description="Minimum seconds to wait between exchange attempts.")

    @classmethod
    def from_domain(
        cls,
        issued: IssuedCliEnrollment,
        *,
        verification_uri: AnyHttpUrl,
    ) -> CliEnrollmentResponse:
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
    public_key_fingerprint: str = Field(
        description="First 20 hex digits of the public key's SHA-256, uppercased in five dash-separated groups. "
        "Compare it against the fingerprint the CLI printed before approving."
    )
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = Field(description="Null until the browser approves this enrollment.")

    @classmethod
    def from_domain(cls, enrollment: CliDeviceEnrollment) -> CliEnrollmentApprovalResponse:
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
    """Prove the enrolled private key and exchange browser approval.

    `signature` covers the ASCII bytes `squid-cli-enrollment-v1`, a NUL byte, the enrollment id as 16
    raw bytes, then the UTF-8 `device_code` prefixed by its big-endian uint16 length.
    """

    device_code: DeviceCode
    signature: Signature

    def signature_bytes(self) -> bytes:
        """Decode the 64-byte Ed25519 signature, raising `InvalidCliDeviceProofError` at any other length."""
        return decode_urlsafe_bytes(self.signature, expected_length=64)


class CliSessionChallengeRequest(StrictSchema):
    """Request a one-time signing nonce for an enrolled device."""

    device_id: UUID


class CliSessionChallengeResponse(StrictSchema):
    """A one-time plaintext proof nonce."""

    id: UUID
    device_id: UUID
    nonce: str = Field(description="Disclosed once. Sign it to obtain a session token.")
    expires_at: datetime = Field(description="When the nonce stops being consumable.")


class CliSessionExchangeRequest(StrictSchema):
    """Exchange an Ed25519-signed proof nonce for a short session.

    `signature` covers the ASCII bytes `squid-cli-session-v1`, a NUL byte, the device id and challenge
    id as 16 raw bytes each, then the UTF-8 `nonce` prefixed by its big-endian uint16 length.
    """

    device_id: UUID
    challenge_id: UUID
    nonce: ProofNonce
    signature: Signature

    def signature_bytes(self) -> bytes:
        """Decode the 64-byte Ed25519 signature, raising `InvalidCliDeviceProofError` at any other length."""
        return decode_urlsafe_bytes(self.signature, expected_length=64)


class CliDeviceResponse(StrictSchema):
    """Account-visible CLI device metadata without its public key."""

    id: UUID
    client_instance_id: UUID
    label: str
    public_key_fingerprint: str = Field(
        description="First 20 hex digits of the public key's SHA-256, uppercased in five dash-separated groups."
    )
    created_at: datetime
    last_used_at: datetime
    revoked_at: datetime | None = Field(description="Null while the device may still open sessions.")

    @classmethod
    def from_domain(cls, device: CliDevice) -> CliDeviceResponse:
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
    token: str = Field(description="Sent as `Authorization: Bearer <token>`. Returned once and never retrievable.")
    expires_at: datetime = Field(description="When the token stops authenticating; obtain a new challenge to renew.")

    @classmethod
    def from_domain(cls, issued: IssuedCliSession) -> IssuedCliSessionResponse:
        return cls(
            device=CliDeviceResponse.from_domain(issued.device),
            session_id=issued.session.id,
            token=issued.token,
            expires_at=issued.session.expires_at.to_stdlib(),
        )
