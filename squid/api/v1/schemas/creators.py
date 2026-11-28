"""Public creator representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from squid.core.accounts.domain import ClaimStatus
from squid.core.accounts.models import AliasClaim, CreatorAlias


class CreatorAliasDetail(BaseModel):
    """A public creator credit with no linked account information."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str

    @classmethod
    def from_domain(cls, alias: CreatorAlias) -> CreatorAliasDetail:
        assert alias.id is not None
        return cls(id=alias.id, name=alias.name)


class CreditClaimRequest(BaseModel):
    credit_ids: list[UUID] = Field(min_length=1)


class CreditClaimApproval(BaseModel):
    credit_ids: list[UUID] = Field(min_length=1)
    reassign: bool = False


class ClaimSelection(BaseModel):
    credit_id: UUID
    build_id: int


class CreditClaimDetail(BaseModel):
    id: int
    name: str
    status: ClaimStatus
    credits: list[ClaimSelection]
    approved_credit_ids: list[UUID]
    claimant_id: UUID | None = None

    @classmethod
    def from_claim(cls, claim: AliasClaim) -> CreditClaimDetail:
        return cls(
            id=claim.id,
            name=claim.alias_name,
            claimant_id=claim.claimant.public_account_id if claim.claimant else None,
            status=claim.status,
            credits=[
                ClaimSelection(credit_id=UUID(key), build_id=item["build_id"])
                for key, item in claim.selected_credits.items()
            ],
            approved_credit_ids=[UUID(key) for key in claim.approved_credit_ids],
        )
