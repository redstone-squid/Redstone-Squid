"""User account domain values."""

from dataclasses import dataclass
from uuid import UUID

from whenever import Instant

CURRENT_CONSENT_VERSION = "2026-08-03"


@dataclass(frozen=True, slots=True)
class UserConsent:
    """Evidence that a user accepted a particular privacy notice."""

    version: str
    granted_at: Instant

    @classmethod
    def grant_current(cls) -> "UserConsent":
        """Create a receipt for the currently published privacy notice."""
        return cls(version=CURRENT_CONSENT_VERSION, granted_at=Instant.now())


@dataclass(slots=True)
class UserAccount:
    """The account information needed by the application layer."""

    discord_id: int | None
    minecraft_uuid: UUID | None
    ign: str | None
    consent: UserConsent | None = None


@dataclass(frozen=True, slots=True)
class VerificationCode:
    """A valid verification code returned by persistence."""

    minecraft_uuid: UUID
    username: str
