"""Connect external sign-in identities and creator credit to one Squid account.

An account is the internal owner of submissions, preferences, and permissions.
An external identity is a provider and its stable subject, such as Discord plus
a user ID or Java plus a UUID. Several identities can belong to one account.

A creator alias is a name credited on builds. Claiming it associates that credit
with an account; it is separate from the identities used to sign in and from the
profile's editable display name. Renaming or unlinking a sign-in identity does
not remove credit previously claimed under an alias.

Linking adds a verified identity to an account. Merging combines two existing
accounts: the surviving account keeps its ID, while the absorbed account's public
creator ID becomes a redirect.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from whenever import Instant

_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*")
"""ASCII decimal without a leading zero.

str.isdigit() also accepts non-ASCII digits, which would give an identifier more
than one spelling."""

MERGE_PROOF_MAX_AGE_SECONDS = 10 * 60
"""Maximum age of an identity proof accepted for a self-service account merge."""


def fold_creator_name(name: str) -> str:
    """Return the normalized spelling used to compare creator names.

    Normalize Unicode compatibility characters with NFKC, trim surrounding space,
    then casefold. For example, `Straße` and `Strasse` both become `strasse`, and
    full-width letters become their ordinary-width equivalents.

    Store this result rather than recreating it with PostgreSQL lower(). Database
    lowercasing depends on collation and does not implement the same comparison.
    """
    return unicodedata.normalize("NFKC", name).strip().casefold()


class IdentityProvider(StrEnum):
    """An independently verified external identity namespace."""

    DISCORD = "discord"
    JAVA = "java"
    BEDROCK = "bedrock"


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


@dataclass(frozen=True, slots=True)
class IdentityDetails:
    """Provider identity details, without stored verification or private account data."""

    provider: IdentityProvider
    subject: str
    display_name: str | None = None


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
class IdentityRefresh:
    """The result of updating a Java identity's name and creator credit.

    Retained aliases remain credited after a rename. A name held by another account
    appears as contested_alias, with opened_claim identifying the staff review request.
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
    """A currently redeemable code identifying one verified Java identity."""

    minecraft_uuid: UUID
    username: str


def _identity_order(identity: AccountIdentity) -> tuple[bool, int, str]:
    return identity.id is not None, identity.id or 0, identity.subject


@dataclass(frozen=True, slots=True)
class CreditPreview:
    """The creator credit a link is about to affect."""

    name: str
    build_count: int
    held_by_public_creator_id: UUID | None = None
    """Public ID of the current holder, or None if the name is unclaimed.

    Linking claims an unclaimed name. If another creator holds it, linking opens
    a staff claim without moving the credit. Ownership is rechecked at commit.
    """

    @property
    def is_contested(self) -> bool:
        """Whether another creator already holds this name."""
        return self.held_by_public_creator_id is not None


@dataclass(frozen=True, slots=True)
class LinkPreview:
    """The Java identity and creator credit associated with a verification code.

    The preview contains no caller identity. Callers separately check whether the
    account receiving the link already has a conflicting Java identity.
    """

    java_uuid: UUID
    username: str
    credit: CreditPreview | None = None
    """`None` when no build credits this name yet, so there is nothing to move."""

    java_uuid_held_elsewhere: bool = False


@dataclass(frozen=True, slots=True)
class LinkReservation:
    """A temporary hold on a verification code and the token needed to release or use it.

    The hold lets a consent prompt show the code's identity before consuming it.
    It reserves the code, not creator credit: ownership is checked again when the
    link commits. The token identifies the hold without recording its caller.
    """

    token: str
    expires_at: Instant
    preview: LinkPreview


if TYPE_CHECKING:
    from squid.accounts.models import AccountIdentity, AliasClaim, CreatorAlias
