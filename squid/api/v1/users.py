"""Public creator-credit read routes."""

from typing import Annotated

from fastapi import APIRouter, Path

from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.v1.schemas.users import CreatorAliasDetail
from squid.users.errors import CreatorAliasNotFoundError

router = APIRouter(prefix="/creator-aliases", tags=["creator aliases"])


@router.get("/{name}", response_model=CreatorAliasDetail, responses=responses(404, 422, 503))
async def get_creator_alias(
    name: Annotated[str, Path(min_length=1, max_length=64)], services: Services
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await services.users.get_creator_alias(name)
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)
