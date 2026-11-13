"""Public creator representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.accounts.models import CreatorAlias


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    name: str
    claimed: bool
    creator_id: UUID | None

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> CreatorAliasDetail:
        return cls(name=alias.name, claimed=alias.is_claimed, creator_id=alias.public_creator_id)
