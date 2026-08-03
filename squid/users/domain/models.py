"""User account domain values."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from whenever import Instant

CURRENT_CONSENT_VERSION = "2026-08-04"

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"
"""Accounts created before this instant predate consent receipts and are grandfathered."""


def normalize_ign(name: str) -> str:
    """Return the comparison form of a Minecraft name.

    Minecraft names are unique case-insensitively, so credit for ``Foo`` and
    ``foo`` belongs to the same person. This must stay identical to the
    ``normalized_name`` generated column on ``creator_aliases``.
    """
    return name.strip().lower()


class ClaimMethod(StrEnum):
    """How an alias came to be attached to an account."""

    VERIFIED_IGN = "verified_ign"
    """Claimed automatically because it matched a verified Minecraft username."""
    STAFF_APPROVED = "staff_approved"
    """Claimed after a staff member approved an explicit request."""
    MIGRATED = "migrated"
    """Claimed by the split of accounts from creator credits."""


class ClaimStatus(StrEnum):
    """Review state of an explicit alias claim."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


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
    id: int | None = None

    @property
    def needs_consent_refresh(self) -> bool:
        """Whether the account must accept the current privacy notice before storing more about them.

        True for a submitter-only row that has never consented, for a legacy
        row holding a Minecraft link without a receipt, and for a receipt
        recorded against a superseded notice version.
        """
        return self.consent is None or self.consent.version != CURRENT_CONSENT_VERSION


@dataclass(frozen=True, slots=True)
class CreatorAlias:
    """A creator name credited on a build, optionally claimed by an account."""

    id: int
    name: str
    user_id: int | None = None
    claimed_at: Instant | None = None
    claim_method: ClaimMethod | None = None

    @property
    def is_claimed(self) -> bool:
        """Whether an account has been credited with this name."""
        return self.user_id is not None


@dataclass(frozen=True, slots=True)
class AliasClaim:
    """A request to be credited under a creator alias, pending staff review."""

    id: int
    alias_id: int
    alias_name: str
    user_id: int
    status: ClaimStatus
    created_at: Instant
    resolved_at: Instant | None = None
    resolved_by_discord_id: int | None = None


@dataclass(frozen=True, slots=True)
class VerificationCode:
    """A valid verification code returned by persistence."""

    minecraft_uuid: UUID
    username: str
