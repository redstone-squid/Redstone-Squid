"""Provider-neutral account domain values."""

# ruff: noqa: RUF002  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from whenever import Instant

CURRENT_CONSENT_VERSION = "2026-08-04"

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"
"""Accounts created before this instant predate consent receipts and are grandfathered."""

MERGE_PROOF_MAX_AGE_SECONDS = 10 * 60
"""Maximum age of an identity proof accepted for a self-service account merge."""


def fold_creator_name(name: str) -> str:
    """Return the comparison form of a creator name.

    NFKC first, so compatibility forms unify and NBSP or ideographic space become U+0020
    before trimming; then strip; then casefold, which is the Unicode operation for caseless
    matching that ``str.lower()`` is not — ``lower`` leaves ``ΣΣ`` as ``σς`` while casefold
    gives ``σσ``.

    This is the only definition of the value, and it is deliberately not reproduced in SQL.
    Postgres ``lower()`` depends on the database's glibc collation (see
    ``pg_database.datcollversion``), and the two foldings disagree about *what collides* in
    both directions: ``Straße``/``Strasse`` collide here but not in SQL, ``I``/``İ`` collide
    in SQL but not here. A second SQL-side column would therefore be a second, conflicting
    notion of creator identity rather than a hedge against this one.
    """
    return unicodedata.normalize("NFKC", name).strip().casefold()


class IdentityProvider(StrEnum):
    """An independently verified external identity namespace."""

    DISCORD = "discord"
    JAVA = "java"
    BEDROCK = "bedrock"


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    """A verified external identity attached to an account."""

    provider: IdentityProvider
    subject: str
    display_name: str | None = None
    verified_at: Instant | None = None
    id: int | None = None

    @classmethod
    def discord(cls, discord_id: int, *, verified_at: Instant | None = None) -> AccountIdentity:
        """Create a canonical Discord identity."""
        if discord_id <= 0:
            msg = "Discord identity subjects must be positive integers."
            raise ValueError(msg)
        return cls(IdentityProvider.DISCORD, str(discord_id), verified_at=verified_at)

    @classmethod
    def java(
        cls,
        minecraft_uuid: UUID,
        *,
        username: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Java Edition identity."""
        return cls(IdentityProvider.JAVA, str(minecraft_uuid), username, verified_at)

    @classmethod
    def bedrock(
        cls,
        xuid: int,
        *,
        gamertag: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Bedrock identity from an unsigned XUID."""
        if not 0 < xuid < 2**64:
            msg = "Bedrock XUIDs must be unsigned 64-bit integers."
            raise ValueError(msg)
        return cls(IdentityProvider.BEDROCK, str(xuid), gamertag, verified_at)

    @property
    def discord_id(self) -> int | None:
        """Return the Discord snowflake represented by this identity, if any."""
        return int(self.subject) if self.provider is IdentityProvider.DISCORD else None

    @property
    def java_uuid(self) -> UUID | None:
        """Return the Java UUID represented by this identity, if any."""
        return UUID(self.subject) if self.provider is IdentityProvider.JAVA else None


@dataclass(frozen=True, slots=True)
class AccountConsent:
    """Evidence that an account accepted a particular privacy notice."""

    version: str
    granted_at: Instant

    @classmethod
    def grant_current(cls) -> AccountConsent:
        """Create a receipt for the currently published privacy notice."""
        return cls(version=CURRENT_CONSENT_VERSION, granted_at=Instant.now())


@dataclass(frozen=True, slots=True)
class Account:
    """One internal principal with any number of verified external identities."""

    identities: tuple[AccountIdentity, ...] = ()
    consent: AccountConsent | None = None
    id: int | None = None
    created_at: Instant | None = None
    public_creator_id: UUID | None = None

    def identity(self, provider: IdentityProvider) -> AccountIdentity | None:
        """Return this account's first identity for *provider*, if present."""
        return next((identity for identity in self.identities if identity.provider is provider), None)

    @property
    def needs_consent_refresh(self) -> bool:
        """Whether the current privacy notice must be accepted before storing more identity data."""
        return self.consent is None or self.consent.version != CURRENT_CONSENT_VERSION


@dataclass(frozen=True, slots=True)
class RecentAccountProof:
    """Evidence that the caller recently authenticated one account."""

    account_id: int
    verified_at: Instant

    def is_recent_at(self, now: Instant, *, max_age_seconds: int = MERGE_PROOF_MAX_AGE_SECONDS) -> bool:
        """Return whether this proof is inside the permitted merge window."""
        age = (now - self.verified_at).total("seconds")
        return 0 <= age <= max_age_seconds


@dataclass(frozen=True, slots=True)
class AccountMerge:
    """The stable identities resulting from a completed account merge."""

    surviving_account_id: int
    absorbed_account_id: int
    surviving_public_creator_id: UUID
    redirected_public_creator_id: UUID


class ClaimMethod(StrEnum):
    """How an alias came to be attached to an account."""

    VERIFIED_IGN = "verified_ign"
    STAFF_APPROVED = "staff_approved"
    MIGRATED = "migrated"


class ClaimStatus(StrEnum):
    """Review state of an explicit alias claim."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CreatorAlias:
    """A creator name credited on a build, optionally claimed by an account."""

    id: int
    name: str
    account_id: int | None = None
    claimed_at: Instant | None = None
    claim_method: ClaimMethod | None = None
    public_creator_id: UUID | None = None

    @property
    def is_claimed(self) -> bool:
        """Whether an account has been credited with this name."""
        return self.account_id is not None


@dataclass(frozen=True, slots=True)
class CreatorProfile:
    """A stable public identity grouping every alias claimed by one account."""

    public_id: UUID
    aliases: tuple[str, ...]
    canonical_public_id: UUID | None = None

    @property
    def was_redirected(self) -> bool:
        """Whether the requested public identifier belonged to a merged account."""
        return self.canonical_public_id is not None and self.canonical_public_id != self.public_id


@dataclass(frozen=True, slots=True)
class AliasClaim:
    """A request to be credited under a creator alias, pending staff review."""

    id: int
    alias_id: int
    alias_name: str
    account_id: int
    status: ClaimStatus
    created_at: Instant
    resolved_at: Instant | None = None
    resolved_by_account_id: int | None = None
    claimant: Account | None = None
    """The claiming account, when the caller asked for it.

    Present so a staff queue can name a claimant as something better than an internal ID,
    without every claim read paying for the join.
    """


@dataclass(frozen=True, slots=True)
class IdentityRefresh:
    """Outcome of reconciling a Java identity's display name with its creator credit.

    Every field is filled on every refresh, including one that changed nothing, so callers
    render one shape rather than inferring what happened from a bare `None`.
    """

    account_id: int
    java_uuid: UUID
    current_name: str
    previous_name: str | None = None
    claimed_alias: CreatorAlias | None = None
    retained_alias_names: tuple[str, ...] = ()
    contested_alias: CreatorAlias | None = None
    opened_claim: AliasClaim | None = None

    @property
    def renamed(self) -> bool:
        """Whether the verified name differs from the one previously stored."""
        return self.previous_name is not None and self.previous_name != self.current_name

    @property
    def is_contested(self) -> bool:
        """Whether the new name is credited to a different account, pending staff review."""
        return self.contested_alias is not None


@dataclass(frozen=True, slots=True)
class VerificationCode:
    """A valid verification code returned by persistence."""

    minecraft_uuid: UUID
    username: str
