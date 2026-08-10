"""Strict HTTP schemas for Minecraft installation and player authorization."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.minecraft_auth.domain import (
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftClientOrigin,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PublicServerProfile,
)

InstallationLabel = Annotated[str, Field(min_length=1, max_length=80)]
DeviceCode = Annotated[str, Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
UserCode = Annotated[str, Field(min_length=8, max_length=32, pattern=r"^[A-Za-z2-7a-z-]+$")]
PkceS256Challenge = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
PkceVerifier = Annotated[str, Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")]


class StrictSchema(BaseModel):
    """Reject fields outside the pinned Minecraft authorization contract."""

    model_config = ConfigDict(extra="forbid")


class ServerProfileSchema(StrictSchema):
    """Explicit public listing and sponsor preferences for a Paper server."""

    enabled: bool = False
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    address: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    website_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        pattern=r"^https?://[^\s]+$",
    )
    sponsor_opt_in: bool = False

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> PublicServerProfile:
        """Convert and trim public metadata before persistence."""
        return PublicServerProfile(
            enabled=self.enabled,
            display_name=_trim(self.display_name),
            address=_trim(self.address),
            description=_trim(self.description),
            website_url=_trim(self.website_url),
            sponsor_opt_in=self.sponsor_opt_in,
        )

    @classmethod
    def from_domain(cls, profile: PublicServerProfile) -> "ServerProfileSchema":
        return cls(
            enabled=profile.enabled,
            display_name=profile.display_name,
            address=profile.address,
            description=profile.description,
            website_url=profile.website_url,
            sponsor_opt_in=profile.sponsor_opt_in,
        )


class InstallationCreateRequest(StrictSchema):
    """Register one account-owned Paper installation."""

    label: InstallationLabel
    profile: ServerProfileSchema = Field(default_factory=ServerProfileSchema)

    @model_validator(mode="after")
    def reject_blank_label(self) -> Self:
        if not self.label.strip():
            msg = "Installation label must not be blank."
            raise ValueError(msg)
        return self


class InstallationResponse(StrictSchema):
    """Account-visible installation metadata without its credential digest."""

    id: UUID
    label: str
    credential_version: int
    profile: ServerProfileSchema
    created_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, installation: PaperInstallation) -> "InstallationResponse":
        return cls(
            id=installation.id,
            label=installation.label,
            credential_version=installation.credential_version,
            profile=ServerProfileSchema.from_domain(installation.profile),
            created_at=installation.created_at.to_stdlib(),
            rotated_at=None if installation.rotated_at is None else installation.rotated_at.to_stdlib(),
            revoked_at=None if installation.revoked_at is None else installation.revoked_at.to_stdlib(),
        )


class InstallationListResponse(StrictSchema):
    """All Paper installations owned by the signed-in account."""

    installations: list[InstallationResponse]


class IssuedInstallationResponse(StrictSchema):
    """Installation metadata and its one-time plaintext secret."""

    installation: InstallationResponse
    secret: str

    @classmethod
    def from_domain(cls, issued: IssuedInstallationCredential) -> "IssuedInstallationResponse":
        parsed = MinecraftSecretCodec.parse_installation_token(issued.token)
        if parsed is None or parsed[0] != issued.installation.id:
            msg = "Issued installation credential did not match its installation."
            raise ValueError(msg)
        return cls(
            installation=InstallationResponse.from_domain(issued.installation),
            secret=parsed[1],
        )


class PaperChallengeCreateRequest(StrictSchema):
    """Request player authorization from an authenticated Paper server."""

    java_uuid: UUID


class FabricChallengeCreateRequest(StrictSchema):
    """Request player authorization from Fabric with an S256 PKCE commitment."""

    java_uuid: UUID
    pkce_s256_challenge: PkceS256Challenge


class ChallengeCreateResponse(StrictSchema):
    """One-time device-flow codes and their polling policy."""

    id: UUID
    device_code: str
    user_code: str
    expires_at: datetime
    polling_interval_seconds: int

    @classmethod
    def from_domain(cls, challenge: IssuedPlayerChallenge) -> "ChallengeCreateResponse":
        return cls(
            id=challenge.id,
            device_code=challenge.device_code,
            user_code=challenge.user_code,
            expires_at=challenge.expires_at.to_stdlib(),
            polling_interval_seconds=challenge.polling_interval_seconds,
        )


class PaperChallengeExchangeRequest(StrictSchema):
    """Exchange an approved Paper challenge on its authenticated installation."""

    device_code: DeviceCode


class FabricChallengeExchangeRequest(StrictSchema):
    """Exchange an approved Fabric challenge with its PKCE verifier."""

    device_code: DeviceCode
    pkce_verifier: PkceVerifier


class ChallengeApprovalRequest(StrictSchema):
    """Approve a displayed user code as the signed-in account."""

    user_code: UserCode


class ChallengeApprovalResponse(StrictSchema):
    """Non-secret confirmation of an exact-identity approval."""

    id: UUID
    java_uuid: UUID
    origin: MinecraftClientOrigin
    approved_at: datetime

    @classmethod
    def from_domain(cls, challenge: PlayerAuthorizationChallenge) -> "ChallengeApprovalResponse":
        if challenge.approved_at is None:
            msg = "Approved challenge response requires an approval timestamp."
            raise ValueError(msg)
        return cls(
            id=challenge.id,
            java_uuid=challenge.java_uuid,
            origin=challenge.origin,
            approved_at=challenge.approved_at.to_stdlib(),
        )


class IssuedPlayerGrantResponse(StrictSchema):
    """One-time player bearer token response from a consumed challenge."""

    grant_id: UUID
    token: str
    java_uuid: UUID
    origin: MinecraftClientOrigin
    installation_id: UUID | None
    expires_at: datetime

    @classmethod
    def from_domain(cls, issued: IssuedPlayerGrant) -> "IssuedPlayerGrantResponse":
        return cls(
            grant_id=issued.grant.id,
            token=issued.token,
            java_uuid=issued.grant.java_uuid,
            origin=issued.grant.origin,
            installation_id=issued.grant.installation_id,
            expires_at=issued.grant.expires_at.to_stdlib(),
        )


def _trim(value: str | None) -> str | None:
    return None if value is None else value.strip()
