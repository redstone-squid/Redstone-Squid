"""Account domain values: one account, many identities."""

# ruff: noqa: RUF002  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from whenever import Instant

from squid.accounts.domain.consent import AccountConsent, consent_refresh_required
from squid.core.errors import ValidationError
from squid.core.i18n import tr

_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*")
"""ASCII decimal without a leading zero. Deliberately not `str.isdigit`, which accepts
non-ASCII digits such as U+0661 that `int()` then happily parses into a different string."""

MERGE_PROOF_MAX_AGE_SECONDS = 10 * 60
"""Maximum age of an identity proof accepted for a self-service account merge."""


def fold_creator_name(name: str) -> str:
    """Return the comparison form of a creator name: NFKC, then strip, then casefold.

    NFKC before stripping so NBSP and ideographic space become U+0020; casefold rather than
    ``str.lower()``, which leaves ``ΣΣ`` as ``σς`` where casefold gives ``σσ``.

    The only definition of the value, deliberately not reproduced in SQL: Postgres ``lower()``
    follows the database's glibc collation and disagrees in both directions (``Straße``/``Strasse``
    collide here but not in SQL, ``I``/``İ`` in SQL but not here), so a SQL-side column would be a
    second, conflicting notion of creator identity.
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
    is_public: bool = True
    """Whether this identity appears on the account's public creator profile.

    Per identity rather than all-or-nothing: publishing an IGN and hiding a Discord account is a
    common combination.
    """

    avatar_key: str | None = None
    """Provider-specific rendering key, set only where the subject is not enough.

    Discord avatar URLs need the hash, which only the gateway knows, so the bot refreshes it. Java
    heads derive from the UUID, so this stays `None` there.
    """

    @classmethod
    def for_provider(
        cls,
        provider: IdentityProvider,
        subject: str,
        *,
        display_name: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Build an identity in *provider*'s canonical subject form.

        The only authority on subject format; the database carries no format constraint. The `match`
        is exhaustive, so adding an `IdentityProvider` member is a type error here until its subject
        format is stated.

        Raises:
            ValidationError: *subject* is not in *provider*'s subject format.
        """
        match provider:
            case IdentityProvider.DISCORD:
                if _POSITIVE_DECIMAL.fullmatch(subject) is None or int(subject) >= 2**63:
                    raise ValidationError(
                        tr(t"Discord identity subjects must be positive signed 64-bit integers, got {subject!r}.")
                    )
                return cls(provider, subject, display_name, verified_at)
            case IdentityProvider.BEDROCK:
                if _POSITIVE_DECIMAL.fullmatch(subject) is None or int(subject) >= 2**64:
                    raise ValidationError(tr(t"Bedrock XUIDs must be unsigned 64-bit integers, got {subject!r}."))
                return cls(provider, subject, display_name, verified_at)
            case IdentityProvider.JAVA:
                # `UUID` also lowercases and hyphenates, so an uppercase or bare-hex
                # subject from an external API normalizes rather than being rejected.
                try:
                    canonical = str(UUID(subject))
                except ValueError as error:
                    raise ValidationError(tr(t"Java identity subjects must be UUIDs, got {subject!r}.")) from error
                return cls(provider, canonical, display_name, verified_at)

    @classmethod
    def discord(cls, discord_id: int, *, verified_at: Instant | None = None) -> AccountIdentity:
        """Create a canonical Discord identity, raising `ValidationError` if the snowflake is out of range."""
        return cls.for_provider(IdentityProvider.DISCORD, str(discord_id), verified_at=verified_at)

    @classmethod
    def java(
        cls,
        minecraft_uuid: UUID,
        *,
        username: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Java Edition identity."""
        return cls.for_provider(
            IdentityProvider.JAVA, str(minecraft_uuid), display_name=username, verified_at=verified_at
        )

    @classmethod
    def bedrock(
        cls,
        xuid: int,
        *,
        gamertag: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Bedrock identity, raising `ValidationError` if the XUID is out of range."""
        return cls.for_provider(IdentityProvider.BEDROCK, str(xuid), display_name=gamertag, verified_at=verified_at)

    @property
    def discord_id(self) -> int | None:
        """The Discord snowflake, or `None` unless this is a Discord identity."""
        return int(self.subject) if self.provider is IdentityProvider.DISCORD else None

    @property
    def java_uuid(self) -> UUID | None:
        """The Java UUID, or `None` unless this is a Java identity."""
        return UUID(self.subject) if self.provider is IdentityProvider.JAVA else None


@dataclass(frozen=True, slots=True)
class Account:
    """One internal caller with any number of verified external identities."""

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
        return consent_refresh_required(
            self.created_at,
            None if self.consent is None else self.consent.version,
        )


@dataclass(frozen=True, slots=True)
class RecentAccountProof:
    """Evidence that the caller recently authenticated one account."""

    account_id: int
    verified_at: Instant

    def is_recent_at(self, now: Instant, *, max_age_seconds: int = MERGE_PROOF_MAX_AGE_SECONDS) -> bool:
        """Return whether this proof is inside the merge window; one dated after *now* is not."""
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
    """The claiming account, filled only when the caller asked for the join."""


@dataclass(frozen=True, slots=True)
class IdentityRefresh:
    """Outcome of reconciling a Java identity's display name with its creator credit.

    Every field is filled on every refresh, including one that changed nothing.
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
        """Whether the verified name differs from the stored one; false when none was stored."""
        return self.previous_name is not None and self.previous_name != self.current_name

    @property
    def is_contested(self) -> bool:
        """Whether the new name is credited to a different account, pending staff review."""
        return self.contested_alias is not None


@dataclass(frozen=True, slots=True)
class VerificationCode:
    """The Java account a redeemed verification code was issued for."""

    minecraft_uuid: UUID
    username: str


@dataclass(frozen=True, slots=True)
class CreditPreview:
    """The creator credit a link is about to affect."""

    name: str
    build_count: int
    held_by_public_creator_id: UUID | None = None
    """`None` means unclaimed, so agreeing attributes the credit to the caller.

    Set means another creator holds it, and agreeing moves nothing: reconciliation never transfers a
    held name, it opens a staff claim.
    """

    @property
    def is_contested(self) -> bool:
        """Whether another creator already holds this name."""
        return self.held_by_public_creator_id is not None


@dataclass(frozen=True, slots=True)
class LinkPreview:
    """What redeeming a held code will do, knowable without spending it.

    Every field is a fact about the code rather than the caller, which is what lets a reservation
    stay anonymous; caller-specific refusals stay checks at the entry point.
    """

    java_uuid: UUID
    username: str
    credit: CreditPreview | None = None
    """`None` when no build credits this name yet, so there is nothing to move."""

    java_uuid_held_elsewhere: bool = False


@dataclass(frozen=True, slots=True)
class LinkReservation:
    """A held verification code, plus the one-time token that commits or releases it.

    Holding the code means the previewed facts are the facts that commit, and gives the attempt cap
    a write to count. The token is the whole authority: nothing here identifies the reserver, so
    cancelling stores nothing about them.
    """

    token: str
    expires_at: Instant
    preview: LinkPreview
