"""Public creator representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from whenever import Instant

from squid.accounts.domain import (
    CreatorAlias,
    CreditedAlias,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
    PublicIdentity,
)


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    name: str
    claimed: bool
    creator_id: UUID | None

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> CreatorAliasDetail:
        return cls(name=alias.name, claimed=alias.is_claimed, creator_id=alias.public_creator_id)


class CreditedAliasDetail(BaseModel):
    """One creator name held by this creator, and how many builds carry it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    build_count: int

    @classmethod
    def from_domain(cls, alias: CreditedAlias) -> CreditedAliasDetail:
        return cls(name=alias.name, build_count=alias.build_count)


class PublicIdentityDetail(BaseModel):
    """A linked identity the creator has chosen to publish.

    Carries no verification timestamp and no internal id: when it was verified is nobody else's
    business, and the id is the handle used to change it.
    """

    model_config = ConfigDict(extra="forbid")

    provider: IdentityProvider
    subject: str
    display_name: str | None

    @classmethod
    def from_domain(cls, identity: PublicIdentity) -> PublicIdentityDetail:
        return cls(provider=identity.provider, subject=identity.subject, display_name=identity.display_name)


class ProfileLinkDetail(BaseModel):
    """One external link published on a creator profile."""

    model_config = ConfigDict(extra="forbid")

    label: str
    url: str

    @classmethod
    def from_domain(cls, link: ProfileLink) -> ProfileLinkDetail:
        return cls(label=link.label, url=link.url)


class CreatorProfileDetail(BaseModel):
    """A creator's public page.

    `hidden` is the shape switch: a hidden profile still serves `id`, `canonical_id` and
    `aliases`, because build credit is a fact about a build and a page that vanished would strand
    every build crediting it. Everything else is null or empty in that case.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    canonical_id: UUID | None
    """Set when the requested id belonged to an account that was merged into this one."""

    hidden: bool
    aliases: list[CreditedAliasDetail]
    display_name: str | None
    bio: str | None
    pronouns: str | None
    links: list[ProfileLinkDetail]
    avatar_url: str | None
    joined_at: Instant | None
    identities: list[PublicIdentityDetail]

    @classmethod
    def from_domain(cls, profile: PublicCreatorProfile) -> CreatorProfileDetail:
        return cls(
            id=profile.public_id,
            canonical_id=profile.canonical_public_id,
            hidden=profile.hidden,
            aliases=[CreditedAliasDetail.from_domain(alias) for alias in profile.aliases],
            display_name=profile.display_name,
            bio=profile.bio,
            pronouns=profile.pronouns,
            links=[ProfileLinkDetail.from_domain(link) for link in profile.links],
            avatar_url=profile.avatar_url,
            joined_at=profile.joined_at,
            identities=[PublicIdentityDetail.from_domain(identity) for identity in profile.identities],
        )
