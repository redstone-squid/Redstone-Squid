"""Public creator-credit read routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from squid.accounts.errors import CreatorAliasNotFoundError
from squid.api.dependencies import Accounts
from squid.api.errors import responses
from squid.api.v1.schemas.users import CreatorAliasDetail, CreatorProfileDetail
from squid.core.errors import NotFoundError

router = APIRouter(prefix="/creator-aliases", tags=["creator aliases"])
profiles_router = APIRouter(prefix="/creators", tags=["creator aliases"])


@router.get("/{name}", response_model=CreatorAliasDetail, responses=responses(404, 422, 503))
async def get_creator_alias(
    name: Annotated[str, Path(min_length=1, max_length=64)], accounts: Accounts
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(name)
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


@profiles_router.get("/{creator_id}", response_model=CreatorProfileDetail, responses=responses(404, 422, 503))
async def get_creator_profile(creator_id: UUID, accounts: Accounts) -> CreatorProfileDetail:
    """Return every public alias grouped under a stable creator identity."""
    profile = await accounts.get_creator_profile(creator_id)
    if profile is None:
        raise NotFoundError(resource="creator", public_context={"creator_id": str(creator_id)})
    return CreatorProfileDetail.from_domain(profile)
