"""Domain values for server credentials and Minecraft player grants."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from whenever import Instant

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid_i18n import md_tr


class MinecraftClientOrigin(StrEnum):
    """A server-established Minecraft client transport."""

    PAPER = "paper"
    FABRIC = "fabric"


@dataclass(frozen=True, slots=True)
class PublicServerProfile:
    """Optional, explicitly published metadata for one Paper server."""

    enabled: bool = False
    display_name: str | None = None
    address: str | None = None
    description: str | None = None
    website_url: str | None = None
    sponsor_opt_in: bool = False

    def __post_init__(self) -> None:
        values = (
            ("display name", self.display_name, 80),
            ("address", self.address, 255),
            ("description", self.description, 500),
            ("website URL", self.website_url, 2048),
        )
        for label, value, maximum in values:
            if value is not None and (not value.strip() or len(value) > maximum):
                raise ValidationError(
                    md_tr(
                        t"minecraft-auth-server-profile-length = Server "
                        t"profile {label} must contain 1 to {maximum} characters.",
                    )
                )
        if self.sponsor_opt_in and not self.enabled:
            raise ValidationError(
                tr(
                    t"minecraft-auth-public-sponsor-opt-in-required = Sponsor opt-in "
                    t"requires an enabled public server profile."
                )
            )


@dataclass(frozen=True, slots=True)
class PublishedPaperServer:
    """Safe public projection of an explicitly listed Paper installation."""

    installation_id: UUID
    profile: PublicServerProfile
    created_at: Instant

    def __post_init__(self) -> None:
        if not self.profile.enabled:
            raise ValidationError(
                tr(
                    t"minecraft-auth-published-paper-server = A published Paper "
                    t"server must have its public profile enabled."
                )
            )


@dataclass(frozen=True, slots=True)
class IssuedInstallationCredential:
    """A Paper installation and the credential disclosed exactly once."""

    installation: PaperInstallation
    token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedPaperInstallation:
    """Trusted Paper caller context produced by credential authentication."""

    id: UUID
    owner_account_id: int
    credential_version: int


@dataclass(frozen=True, slots=True)
class IssuedPlayerChallenge:
    """Device-flow codes returned only when a challenge is created."""

    id: UUID
    device_code: str
    user_code: str
    expires_at: Instant
    polling_interval_seconds: int


@dataclass(frozen=True, slots=True)
class IssuedPlayerGrant:
    """A player grant and its one-time plaintext token response."""

    grant: PlayerGrant
    token: str


@dataclass(frozen=True, slots=True)
class MinecraftPlayerContext:
    """Trusted identity context consumed by submission application services."""

    grant_id: UUID
    account_id: int
    java_uuid: UUID
    origin: MinecraftClientOrigin
    installation_id: UUID | None = None


if TYPE_CHECKING:
    from squid.minecraft_auth.models import PaperInstallation, PlayerGrant
