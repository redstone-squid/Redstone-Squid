"""Creator profile content and visibility rules.

Profile text is separate from CreatorAlias build credit. Changing a display name
does not move credit. present_public_profile applies visibility before a profile
is exposed through any transport.
"""

import unicodedata
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit
from uuid import UUID

from whenever import Instant

from squid.accounts.domain.models import IdentityProvider
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.core.sentinel import UNSET
from squid_ui.text import Message

MAX_PROFILE_LINKS: Final = 10
"""Maximum number of external links on one profile."""

MAX_LINK_LABEL_LENGTH: Final = 32
MAX_LINK_URL_LENGTH: Final = 2048
MAX_DISPLAY_NAME_LENGTH: Final = 64
MAX_BIO_LENGTH: Final = 500
MAX_PRONOUNS_LENGTH: Final = 40

PROFILE_LINK_SCHEMES: Final = frozenset({"https"})
"""Allowed schemes for public links. HTTPS excludes script and data URLs as well as
unencrypted navigation."""

JAVA_AVATAR_URL_TEMPLATE: Final = "https://mc-heads.net/avatar/{subject}"
"""Java heads derive from the UUID alone, so they need nothing stored."""

DISCORD_AVATAR_URL_TEMPLATE: Final = "https://cdn.discordapp.com/avatars/{subject}/{key}.png"
"""Discord needs the avatar hash, which only the gateway knows: see `AccountIdentity.avatar_key`."""

MERGE_CODE_BYTES: Final = 5
"""Random bytes in a merge code, rendered as eight base32 characters.

The short code is intended for typing between sessions. One live ticket per
account, expiry, and route rate limits restrict guessing."""

MERGE_TICKET_TTL_SECONDS: Final = 10 * 60
"""Lifetime of a merge ticket, matching MERGE_PROOF_MAX_AGE_SECONDS.

Ticket creation supplies the absorbed account's proof timestamp during a merge."""


def _reject_control_characters(value: str, *, field_name: str | Message, allow_newlines: bool = False) -> None:
    """Reject Unicode control and format characters, optionally allowing line breaks."""
    for character in value:
        if character in "\n\r" and allow_newlines:
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            field = field_name
            raise ValidationError(tr(t"{field} may not contain control characters."))


def _normalize_text(
    value: str | None, *, field_name: str | Message, limit: int, allow_newlines: bool = False
) -> str | None:
    """Normalize Unicode and trim a profile field; return None for blank or missing text."""
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
        clean_label = _normalize_text(label, field_name=tr("Link label"), limit=MAX_LINK_LABEL_LENGTH)
        if clean_label is None:
            raise ValidationError(tr("Every link needs a label."))
        clean_url = url.strip()
        if not clean_url:
            raise ValidationError(tr("Every link needs a URL."))
        if len(clean_url) > MAX_LINK_URL_LENGTH:
            limit = MAX_LINK_URL_LENGTH
            raise ValidationError(tr(t"Link URLs may be at most {limit} characters."))
        _reject_control_characters(clean_url, field_name=tr("Link URL"))
        parts = urlsplit(clean_url)
        if parts.scheme not in PROFILE_LINK_SCHEMES:
            scheme = parts.scheme or ""
            raise ValidationError(tr(t"Links must be https URLs, got {scheme!r}."))
        if not parts.hostname:
            raise ValidationError(tr("Links must include a hostname."))
        if "@" in parts.netloc:
            # Credentials in the authority are the classic way to make a link read as one host
            # while resolving to another.
            raise ValidationError(tr("Links may not carry credentials."))
        return cls(label=clean_label, url=clean_url)


type Patch[T] = T | UNSET


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
            value is UNSET
            for value in (self.display_name, self.bio, self.pronouns, self.links, self.hidden, self.avatar_identity_id)
        )

    def validated(self) -> ProfileUpdate:
        """Return an update with supplied text and links normalized and validated.

        Omitted fields remain UNSET. Empty text becomes None; links are re-parsed so
        constructing a ProfileLink directly cannot bypass URL validation.
        """
        changes: dict[str, object] = {}
        if self.display_name is not UNSET:
            changes["display_name"] = _normalize_text(
                self.display_name, field_name=tr("Display name"), limit=MAX_DISPLAY_NAME_LENGTH
            )
        if self.bio is not UNSET:
            changes["bio"] = _normalize_text(self.bio, field_name=tr("Bio"), limit=MAX_BIO_LENGTH, allow_newlines=True)
        if self.pronouns is not UNSET:
            changes["pronouns"] = _normalize_text(self.pronouns, field_name=tr("Pronouns"), limit=MAX_PRONOUNS_LENGTH)
        if self.links is not UNSET:
            links = tuple(self.links)
            if len(links) > MAX_PROFILE_LINKS:
                limit = MAX_PROFILE_LINKS
                count = len(links)
                raise ValidationError(tr(t"A profile may have at most {limit} links, got {count}."))
            changes["links"] = tuple(ProfileLink.parse(link.label, link.url) for link in links)
        return replace(self, **changes)  # type: ignore[arg-type]

    def apply(self, profile: AccountProfile) -> AccountProfile:
        """Apply the present fields to the profile and return it."""
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
            if value is not UNSET
        }
        for name, value in changes.items():
            setattr(profile, name, value)
        return profile


@dataclass(frozen=True, slots=True)
class CreditedAlias:
    """A creator name held by an account, with how many builds carry it."""

    name: str
    build_count: int = 0


@dataclass(frozen=True, slots=True)
class PublicIdentity:
    """Public provider identity details, without verification times or internal row IDs."""

    provider: IdentityProvider
    subject: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class CreatorProfileRecord:
    """A creator's profile, identities, and credits before visibility is applied.

    Pass through present_public_profile before exposing this record publicly.
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
    """Return the public view of a creator profile.

    A hidden profile still exposes its public ID, aliases, and build counts so
    build attribution remains available. Other profile fields are omitted. Visible
    profiles include only public identities, including when choosing an avatar.
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
class MergePreview:
    """The identities and build credits that a merge would move, shown before confirmation."""

    absorbed_public_creator_id: UUID
    alias_names: tuple[str, ...] = ()
    identity_count: int = 0
    build_count: int = 0


if TYPE_CHECKING:
    from squid.accounts.models import AccountIdentity, AccountProfile
