"""Public creator credit routes."""

from typing import Annotated

from litestar import Router, get
from litestar.di import Provide
from litestar.params import PathParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Accounts, CurrentCaller, get_accounts
from squid.api.errors import responses
from squid.api.v1.schemas.creators import CreatorAliasDetail
from squid.core.accounts.errors import CreatorAliasNotFoundError


@get(
    "/{name:str}",
    responses=responses(404, 422, 503),
    operation_id="creator_alias_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_creator_alias(
    caller: CurrentCaller, name: Annotated[str, PathParameter(min_length=1, max_length=64)], accounts: Accounts
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(name, context=caller.context())
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


router = Router(
    dependencies={"accounts": Provide(get_accounts, sync_to_thread=False)},
    path="/creator-aliases",
    tags=["creators"],
    route_handlers=[get_creator_alias],
)
