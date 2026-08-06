"""Public creator-credit representations."""

from pydantic import BaseModel, ConfigDict

from squid.users.domain import CreatorAlias


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    name: str
    claimed: bool

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> "CreatorAliasDetail":
        return cls(name=alias.name, claimed=alias.is_claimed)
