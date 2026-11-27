"""Public creator representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.core.accounts.models import CreatorAlias


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    claimed: bool
    account_id: UUID | None
    """The public ID of the account holding this name, if claimed."""

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> CreatorAliasDetail:
        assert alias.id is not None
        return cls(id=alias.id, name=alias.name, claimed=alias.is_claimed, account_id=alias.public_account_id)
