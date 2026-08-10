"""Public creator-credit representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.users.domain import CreatorAlias, CreatorProfile


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    name: str
    claimed: bool
    creator_id: UUID | None

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> "CreatorAliasDetail":
        return cls(name=alias.name, claimed=alias.is_claimed, creator_id=alias.public_creator_id)


class CreatorProfileDetail(BaseModel):
    """A stable public creator identity with all of its claimed aliases."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    aliases: list[str]

    @classmethod
    def from_domain(cls, profile: CreatorProfile) -> "CreatorProfileDetail":
        return cls(id=profile.public_id, aliases=list(profile.aliases))
