"""Strict HTTP schemas for Minecraft installation and player authorization."""

from datetime import datetime
from typing import Annotated, Self
from urllib.parse import urlencode
from uuid import UUID

from pydantic import AnyHttpUrl, ConfigDict, Field, model_validator

from squid.api.schema import ApiSchema
from squid.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.minecraft_auth.domain import (
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftClientOrigin,
    OwnedPaperInstallation,
    PlayerAuthorizationChallenge,
    PublicServerProfile,
)

InstallationLabel = Annotated[
    str, Field(min_length=1, max_length=80, description="Free text shown to the account owner.")
]
DeviceCode = Annotated[
    str,
    Field(
        min_length=32,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The `device_code` returned when the challenge was created, echoed verbatim.",
    ),
]
UserCode = Annotated[
    str,
    Field(
        min_length=16,
        max_length=19,
        pattern=r"^[A-Za-z2-7]{4}(?:-?[A-Za-z2-7]{4}){3}$",
        description="The code displayed to the player: sixteen RFC 4648 base32 characters, issued in "
        "dash-separated groups of four. Case is ignored and the dashes may be omitted.",
    ),
]
PkceS256Challenge = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_-]{43}$",
        description="RFC 7636 S256 commitment: the SHA-256 of the verifier, unpadded URL-safe base64. Only S256 is "
        "accepted; `plain` is not.",
    ),
]
PkceVerifier = Annotated[
    str,
    Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]+$",
        description="The RFC 7636 verifier whose SHA-256 was sent as `pkce_s256_challenge`.",
    ),
]


class StrictSchema(ApiSchema):
    """Reject fields outside the pinned Minecraft authorization contract."""

    model_config = ConfigDict(extra="forbid")


class ServerProfileSchema(StrictSchema):
    """Explicit public listing and sponsor preferences for a Paper server.

    Every text field is trimmed before storage; nothing here is published until `enabled` is true.
    """

    enabled: bool = Field(default=False, description="Whether this installation appears in the public server list.")
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    address: str | None = Field(default=None, min_length=1, max_length=255, description="Address players connect to.")
    description: str | None = Field(default=None, min_length=1, max_length=500)
    website_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        pattern=r"^https?://[^\s]+$",
    )
    sponsor_opt_in: bool = Field(
        default=False,
        description="Whether builds submitted through this installation carry it as their sponsor. The attribution "
        "is captured at finalization and does not change afterwards.",
    )

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
    def from_domain(cls, profile: PublicServerProfile) -> ServerProfileSchema:
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
    credential_version: int = Field(
        description="Rises by one on every rotation, starting at 1. The previous secret stops authenticating, and "
        "idempotency keys are namespaced by it, so a rotation starts a fresh key space."
    )
    profile: ServerProfileSchema
    created_at: datetime
    rotated_at: datetime | None = Field(description="Null until the credential is first rotated.")
    revoked_at: datetime | None = Field(description="Null while the installation may still authenticate.")

    @classmethod
    def from_domain(cls, installation: OwnedPaperInstallation) -> InstallationResponse:
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
    secret: str = Field(
        description="Disclosed once and never retrievable. Send it with the installation id in the "
        "`Squid-Installation-ID` and `Squid-Installation-Secret` headers."
    )

    @classmethod
    def from_domain(cls, issued: IssuedInstallationCredential) -> IssuedInstallationResponse:
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

    java_uuid: UUID = Field(description="Java Edition UUID of the player being authorized.")


class FabricChallengeCreateRequest(StrictSchema):
    """Request player authorization from Fabric with an S256 PKCE commitment.

    Fabric has no installation credential, so the PKCE pair is what binds the exchange to the client
    that opened the challenge.
    """

    java_uuid: UUID = Field(description="Java Edition UUID of the player being authorized.")
    pkce_s256_challenge: PkceS256Challenge


class ChallengeCreateResponse(StrictSchema):
    """One-time device-flow codes and their polling policy."""

    id: UUID
    device_code: str = Field(
        description="Secret half of the challenge, disclosed once. Sent back at exchange, and never displayed."
    )
    user_code: str = Field(description="Code the player types into the browser, in dash-separated groups of four.")
    verification_uri: AnyHttpUrl = Field(description="Browser page where the player enters `user_code`.")
    verification_uri_complete: AnyHttpUrl = Field(
        description="Same page with `user_code` in the query string, so a follow can skip typing it."
    )
    expires_at: datetime = Field(
        description="When the challenge stops being approvable or exchangeable; start a new one afterwards."
    )
    polling_interval_seconds: int = Field(description="Minimum seconds to wait between exchange attempts.")

    @classmethod
    def from_domain(
        cls,
        challenge: IssuedPlayerChallenge,
        *,
        verification_uri: AnyHttpUrl,
    ) -> ChallengeCreateResponse:
        separator = "&" if verification_uri.query else "?"
        return cls(
            id=challenge.id,
            device_code=challenge.device_code,
            user_code=challenge.user_code,
            verification_uri=verification_uri,
            verification_uri_complete=AnyHttpUrl(
                f"{verification_uri}{separator}{urlencode({'code': challenge.user_code})}"
            ),
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
    java_uuid: UUID = Field(description="Java Edition UUID the approval is bound to.")
    origin: MinecraftClientOrigin = Field(description="Which client opened the challenge: `paper` or `fabric`.")
    approved_at: datetime

    @classmethod
    def from_domain(cls, challenge: PlayerAuthorizationChallenge) -> ChallengeApprovalResponse:
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
    token: str = Field(description="Sent as `Authorization: Bearer <token>`. Returned once and never retrievable.")
    java_uuid: UUID
    origin: MinecraftClientOrigin = Field(description="Which client the grant was issued to: `paper` or `fabric`.")
    installation_id: UUID | None = Field(
        description="The Paper installation that vouched for the player. Always set when `origin` is `paper` and "
        "always null when it is `fabric`."
    )
    expires_at: datetime = Field(description="When the token stops authenticating; open a new challenge to renew.")

    @classmethod
    def from_domain(cls, issued: IssuedPlayerGrant) -> IssuedPlayerGrantResponse:
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
