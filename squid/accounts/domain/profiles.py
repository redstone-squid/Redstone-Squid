"""Public creator profiles: what an account chooses to show, and what it hides.

A profile is presentation, deliberately separate from `CreatorAlias`, which is build credit.
Renaming yourself here moves no credit and needs no staff review; the public page shows both, so
attribution never depends on what somebody calls themself this week.

Visibility lives here rather than in the API layer because three transports read it. The one
authority is `present_public_profile`: everything else stores flags.
"""

import unicodedata
from dataclasses import dataclass, replace
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from whenever import Instant

from squid.accounts.domain.models import AccountIdentity, IdentityProvider
from squid.core.errors import ValidationError
from squid.core.i18n import _, tr

MAX_PROFILE_LINKS: Final = 10
"""Links per profile. A profile is an introduction, not a link tree."""

MAX_LINK_LABEL_LENGTH: Final = 32
MAX_LINK_URL_LENGTH: Final = 2048
MAX_DISPLAY_NAME_LENGTH: Final = 64
MAX_BIO_LENGTH: Final = 500
MAX_PRONOUNS_LENGTH: Final = 40

PROFILE_LINK_SCHEMES: Final = frozenset({"https"})
"""Only `https`. `http` invites a downgrade on a link we render for other people, and every
other scheme (`javascript:`, `data:`) is an attack looking for a renderer that forgets to escape."""

JAVA_AVATAR_URL_TEMPLATE: Final = "https://mc-heads.net/avatar/{subject}"
"""Java heads derive from the UUID alone, so they need nothing stored."""

DISCORD_AVATAR_URL_TEMPLATE: Final = "https://cdn.discordapp.com/avatars/{subject}/{key}.png"
"""Discord needs the avatar hash, which only the gateway knows: see `AccountIdentity.avatar_key`."""

MERGE_CODE_BYTES: Final = 5
"""40 bits, rendered as 8 base32 characters. Short enough to retype across two sessions, and
guessing is bounded well before entropy matters: one live ticket per account, route rate limits,
and a ten-minute expiry."""

MERGE_TICKET_TTL_SECONDS: Final = 10 * 60
"""Deliberately equal to `MERGE_PROOF_MAX_AGE_SECONDS`, so a ticket is redeemable for exactly as
long as the proof it stands for is accepted. Two windows that could disagree would be a bug
waiting for someone to tune one of them."""


def _reject_control_characters(value: str, *, field_name: str, allow_newlines: bool = False) -> None:
    """Refuse characters that would let profile text escape whatever renders it."""
    for character in value:
        if character in "\n\r" and allow_newlines:
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            field = field_name
            raise ValidationError(tr(t"{field} may not contain control characters."))


def _normalize_text(value: str | None, *, field_name: str, limit: int, allow_newlines: bool = False) -> str | None:
    """Normalize one free-text profile field, returning `None` when it is effectively empty.

    Empty and absent are the same thing for profile text: somebody who clears their bio wants it
    gone, not stored as `""` for every reader to special-case.
    """
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    if len(normalized) > limit:
        field = field_name
        length = len(normalized)
        raise ValidationError(tr(t"{field} may be at most {limit} characters, got {length}."))
    _reject_control_characters(normalized, field_name=field_name, allow_newlines=allow_newlines)
    return normalized


@dataclass(frozen=True, slots=True)
class ProfileLink:
    """One external link a creator publishes."""

    label: str
    url: str

    @classmethod
    def parse(cls, label: str, url: str) -> ProfileLink:
        """Build a validated link, or raise `ValidationError` explaining which half is wrong."""
        clean_label = _normalize_text(label, field_name=_("Link label"), limit=MAX_LINK_LABEL_LENGTH)
        if clean_label is None:
            raise ValidationError(_("Every link needs a label."))
        clean_url = url.strip()
        if not clean_url:
            raise ValidationError(_("Every link needs a URL."))
        if len(clean_url) > MAX_LINK_URL_LENGTH:
            limit = MAX_LINK_URL_LENGTH
            raise ValidationError(tr(t"Link URLs may be at most {limit} characters."))
        _reject_control_characters(clean_url, field_name=_("Link URL"))
        parts = urlsplit(clean_url)
        if parts.scheme not in PROFILE_LINK_SCHEMES:
            scheme = parts.scheme or ""
            raise ValidationError(tr(t"Links must be https URLs, got {scheme!r}."))
        if not parts.hostname:
            raise ValidationError(_("Links must include a hostname."))
        if "@" in parts.netloc:
            # Credentials in the authority are the classic way to make a link read as one host
            # while resolving to another.
            raise ValidationError(_("Links may not carry credentials."))
        return cls(label=clean_label, url=clean_url)


@dataclass(frozen=True, slots=True)
class AccountProfile:
    """The presentation an account has chosen for itself.

    Every field is optional: a brand-new account has a profile row with nothing in it, which is
    still a valid public profile showing its aliases and build credits.
    """

    account_id: int
    display_name: str | None = None
    bio: str | None = None
    pronouns: str | None = None
    links: tuple[ProfileLink, ...] = ()
    hidden: bool = False
    avatar_identity_id: int | None = None
    updated_at: Instant | None = None

    @classmethod
    def empty(cls, account_id: int) -> AccountProfile:
        """Return the profile every account starts with: public, and saying nothing."""
        return cls(account_id=account_id)


class _Unset:
    """Sentinel type distinguishing "leave alone" from "set to null" in a patch."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()
"""`None` means "clear this field" in a `ProfileUpdate`, so absence needs its own value."""

type Patch[T] = T | _Unset


@dataclass(frozen=True, slots=True)
class ProfileUpdate:
    """A partial profile edit, where clearing a field and omitting it are different requests."""

    display_name: Patch[str | None] = UNSET
    bio: Patch[str | None] = UNSET
    pronouns: Patch[str | None] = UNSET
    links: Patch[tuple[ProfileLink, ...]] = UNSET
    hidden: Patch[bool] = UNSET
    avatar_identity_id: Patch[int | None] = UNSET

    @property
    def is_empty(self) -> bool:
        """Whether this update would change nothing."""
        return all(
            isinstance(value, _Unset)
            for value in (self.display_name, self.bio, self.pronouns, self.links, self.hidden, self.avatar_identity_id)
        )

    def validated(self) -> ProfileUpdate:
        """Return this update with every present field normalized, or raise `ValidationError`.

        Normalization is the domain's job for the same reason `fold_creator_name` is: the API, the
        bot, and any future importer must agree on what "the same display name" means, and three
        transports normalizing independently is three chances to disagree.
        """
        changes: dict[str, object] = {}
        if not isinstance(self.display_name, _Unset):
            changes["display_name"] = _normalize_text(
                self.display_name, field_name=_("Display name"), limit=MAX_DISPLAY_NAME_LENGTH
            )
        if not isinstance(self.bio, _Unset):
            changes["bio"] = _normalize_text(self.bio, field_name=_("Bio"), limit=MAX_BIO_LENGTH, allow_newlines=True)
        if not isinstance(self.pronouns, _Unset):
            changes["pronouns"] = _normalize_text(self.pronouns, field_name=_("Pronouns"), limit=MAX_PRONOUNS_LENGTH)
        if not isinstance(self.links, _Unset):
            links = tuple(self.links)
            if len(links) > MAX_PROFILE_LINKS:
                limit = MAX_PROFILE_LINKS
                count = len(links)
                raise ValidationError(tr(t"A profile may have at most {limit} links, got {count}."))
            changes["links"] = tuple(ProfileLink.parse(link.label, link.url) for link in links)
        return replace(self, **changes)  # type: ignore[arg-type]

    def apply(self, profile: AccountProfile) -> AccountProfile:
        """Return *profile* with this update's present fields written over it."""
        changes = {
            name: value
            for name, value in (
                ("display_name", self.display_name),
                ("bio", self.bio),
                ("pronouns", self.pronouns),
                ("links", self.links),
                ("hidden", self.hidden),
                ("avatar_identity_id", self.avatar_identity_id),
            )
            if not isinstance(value, _Unset)
        }
        return replace(profile, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CreditedAlias:
    """A creator name held by an account, with how many builds carry it."""

    name: str
    build_count: int = 0


@dataclass(frozen=True, slots=True)
class PublicIdentity:
    """The public projection of a linked identity.

    Carries no `verified_at` and no internal id: when it was verified is nobody else's business,
    and the id is the handle used to *change* the identity.
    """

    provider: IdentityProvider
    subject: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class CreatorProfileRecord:
    """Everything persistence knows about one creator, before visibility is applied.

    Deliberately unfiltered: `present_public_profile` is the single place that decides what a
    stranger sees, and it can only be the single place if it is handed the whole truth.
    """

    public_id: UUID
    account_id: int
    profile: AccountProfile
    identities: tuple[AccountIdentity, ...] = ()
    aliases: tuple[CreditedAlias, ...] = ()
    joined_at: Instant | None = None
    canonical_public_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PublicCreatorProfile:
    """What a stranger sees. Fields absent from a hidden profile are `None` or empty."""

    public_id: UUID
    hidden: bool
    aliases: tuple[CreditedAlias, ...] = ()
    canonical_public_id: UUID | None = None
    display_name: str | None = None
    bio: str | None = None
    pronouns: str | None = None
    links: tuple[ProfileLink, ...] = ()
    avatar_url: str | None = None
    joined_at: Instant | None = None
    identities: tuple[PublicIdentity, ...] = ()

    @property
    def was_redirected(self) -> bool:
        """Whether the requested public identifier belonged to a merged account."""
        return self.canonical_public_id is not None and self.canonical_public_id != self.public_id


def avatar_url_for(identity: AccountIdentity) -> str | None:
    """Return the rendered avatar for *identity*, or `None` when the provider cannot supply one."""
    match identity.provider:
        case IdentityProvider.JAVA:
            return JAVA_AVATAR_URL_TEMPLATE.format(subject=identity.subject)
        case IdentityProvider.DISCORD:
            if identity.avatar_key is None:
                return None
            return DISCORD_AVATAR_URL_TEMPLATE.format(subject=identity.subject, key=identity.avatar_key)
        case IdentityProvider.BEDROCK:
            return None


def present_public_profile(record: CreatorProfileRecord) -> PublicCreatorProfile:
    """Apply visibility to *record*, producing what an anonymous reader may see.

    Hiding a profile degrades it to aliases and credits rather than removing it. Build credit is a
    fact about a build, and a creator page that vanished would strand every build crediting it;
    what hiding withholds is the person behind the name, not the name.
    """
    aliases = record.aliases
    if record.profile.hidden:
        return PublicCreatorProfile(
            public_id=record.public_id,
            hidden=True,
            aliases=aliases,
            canonical_public_id=record.canonical_public_id,
        )

    visible = tuple(identity for identity in record.identities if identity.is_public)
    avatar_source = next(
        (identity for identity in visible if identity.id == record.profile.avatar_identity_id),
        None,
    )
    # A hidden identity must not leak through its own avatar: a Java head is the UUID, and a
    # Discord avatar URL is the snowflake.
    avatar_url = None if avatar_source is None else avatar_url_for(avatar_source)
    return PublicCreatorProfile(
        public_id=record.public_id,
        hidden=False,
        aliases=aliases,
        canonical_public_id=record.canonical_public_id,
        display_name=record.profile.display_name,
        bio=record.profile.bio,
        pronouns=record.profile.pronouns,
        links=record.profile.links,
        avatar_url=avatar_url,
        joined_at=record.joined_at,
        identities=tuple(
            PublicIdentity(provider=identity.provider, subject=identity.subject, display_name=identity.display_name)
            for identity in visible
        ),
    )


@dataclass(frozen=True, slots=True)
class MergeTicket:
    """A live claim that one account authenticated recently and consents to being absorbed.

    The plaintext code is returned once, at mint time, and never stored: persistence keeps only a
    digest, exactly like a verification code.
    """

    account_id: int
    created_at: Instant
    expires_at: Instant

    def is_live_at(self, now: Instant) -> bool:
        """Whether this ticket may still be redeemed."""
        return now < self.expires_at


@dataclass(frozen=True, slots=True)
class MergePreview:
    """What completing a merge would do, shown before the irreversible button.

    A merge cannot be undone — the absorbed account's public id becomes a permanent redirect — so
    both surfaces show this first.
    """

    absorbed_public_creator_id: UUID
    alias_names: tuple[str, ...] = ()
    identity_count: int = 0
    build_count: int = 0
