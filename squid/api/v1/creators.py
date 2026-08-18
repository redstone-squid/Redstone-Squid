"""Public creator read routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from squid.accounts.errors import CreatorAliasNotFoundError, CreatorNotFoundError
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Accounts
from squid.api.errors import responses
from squid.api.v1.schemas.creators import CreatorAliasDetail, CreatorProfileDetail

router = APIRouter(prefix="/creator-aliases", tags=["creators"])
profiles_router = APIRouter(prefix="/creators", tags=["creators"])


@router.get(
    "/{name}",
    response_model=CreatorAliasDetail,
    responses=responses(404, 422, 503),
    operation_id="creator_alias_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_creator_alias(
    name: Annotated[str, Path(min_length=1, max_length=64)], accounts: Accounts
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(name)
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


@profiles_router.get(
    "/{creator_id}",
    response_model=CreatorProfileDetail,
    responses=responses(404, 422, 503),
    operation_id="creator_profile_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_creator_profile(creator_id: UUID, accounts: Accounts) -> CreatorProfileDetail:
    """Return a creator's public page, following permanent merge redirects.

    Visibility is applied in the domain, not here: `present_public_profile` is the one authority
    on what a stranger sees, so this route cannot accidentally disagree with the bot about it.
    """
    profile = await accounts.get_public_profile(creator_id)
    if profile is None:
        raise CreatorNotFoundError(creator_id)
    return CreatorProfileDetail.from_domain(profile)
