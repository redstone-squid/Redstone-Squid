"""Public creator read routes."""

from typing import Annotated
from uuid import UUID

from litestar import Router, get
from litestar.params import FromPath, PathParameter

from squid.accounts.errors import CreatorAliasNotFoundError, CreatorNotFoundError
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Accounts
from squid.api.errors import responses
from squid.api.v1.schemas.creators import CreatorAliasDetail, CreatorProfileDetail


@get(
    "/{name:str}",
    responses=responses(404, 422, 503),
    operation_id="creator_alias_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_creator_alias(
    name: Annotated[str, PathParameter(min_length=1, max_length=64)], accounts: Accounts
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(name)
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


@get(
    "/{creator_id:uuid}",
    responses=responses(404, 422, 503),
    operation_id="creator_profile_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_creator_profile(creator_id: FromPath[UUID], accounts: Accounts) -> CreatorProfileDetail:
    """Return a creator's public profile, following account-merge redirects.

    The account service applies profile and identity visibility before this route
    serializes the result.
    """
    profile = await accounts.get_public_profile(creator_id)
    if profile is None:
        raise CreatorNotFoundError(creator_id)
    return CreatorProfileDetail.from_domain(profile)


router = Router(path="/creator-aliases", tags=["creators"], route_handlers=[get_creator_alias])
profiles_router = Router(path="/creators", tags=["creators"], route_handlers=[get_creator_profile])
