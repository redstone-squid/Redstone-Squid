"""Domain values for server credentials and Minecraft player grants."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from whenever import Instant

from squid.core.errors import ValidationError
from squid.core.i18n import _


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
                msg = _("Server profile {label} must contain 1 to {maximum} characters.")
                raise ValidationError(msg, message_params={"label": label, "maximum": maximum})
        if self.sponsor_opt_in and not self.enabled:
            msg = _("Sponsor opt-in requires an enabled public server profile.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class PaperInstallation:
    """An account-owned Paper server without a recoverable secret."""

    id: UUID
    owner_account_id: int
    label: str
    secret_hash: bytes
    credential_version: int
    profile: PublicServerProfile
    created_at: Instant
    rotated_at: Instant | None = None
    revoked_at: Instant | None = None

    def is_active_at(self, _instant: Instant) -> bool:
        """Return whether this installation has not been revoked."""
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class PublishedPaperServer:
    """Safe public projection of an explicitly listed Paper installation."""

    installation_id: UUID
    profile: PublicServerProfile
    created_at: Instant

    def __post_init__(self) -> None:
        if not self.profile.enabled:
            msg = _("A published Paper server must have its public profile enabled.")
            raise ValidationError(msg)


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
class PlayerAuthorizationChallenge:
    """A persisted device authorization request containing only code digests."""

    id: UUID
    device_code_hash: bytes
    user_code_hash: bytes
    origin: MinecraftClientOrigin
    java_uuid: UUID
    created_at: Instant
    expires_at: Instant
    installation_id: UUID | None = None
    installation_credential_version: int | None = None
    pkce_s256_challenge: str | None = None
    approved_by_account_id: int | None = None
    approved_at: Instant | None = None
    exchanged_at: Instant | None = None
    revoked_at: Instant | None = None

    def is_expired_at(self, instant: Instant) -> bool:
        """Return whether the challenge can no longer be approved or exchanged."""
        return self.expires_at <= instant


@dataclass(frozen=True, slots=True)
class IssuedPlayerChallenge:
    """Device-flow codes returned only when a challenge is created."""

    id: UUID
    device_code: str
    user_code: str
    expires_at: Instant
    polling_interval_seconds: int


@dataclass(frozen=True, slots=True)
class PlayerGrant:
    """A short-lived Minecraft player grant containing only a token digest."""

    id: UUID
    challenge_id: UUID
    token_hash: bytes
    account_id: int
    java_uuid: UUID
    origin: MinecraftClientOrigin
    issued_at: Instant
    expires_at: Instant
    installation_id: UUID | None = None
    installation_credential_version: int | None = None
    revoked_at: Instant | None = None

    def is_active_at(self, instant: Instant) -> bool:
        """Return whether the bearer grant is inside its validity window."""
        return self.revoked_at is None and self.issued_at <= instant < self.expires_at


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
