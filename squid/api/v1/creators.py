"""Public creator credit routes."""

from dataclasses import dataclass
from typing import Annotated

from litestar import Router, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, JSONBody, QueryParameter, SkipValidation

from squid.api.contract import ANONYMOUS, DEVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import Accounts, BuildQueries, CurrentCaller, get_accounts, get_build_queries
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.security import requires
from squid.api.v1.schemas.creators import CreatorAliasDetail, CreditClaimApproval, CreditClaimDetail, CreditClaimRequest
from squid.core.accounts.errors import CreatorAliasNotFoundError
from squid.core.builds.application.queries import CreatorStats
from squid.core.builds.domain import BuildListFilters
from squid.core.pagination import ChronologicalSort
from squid.core.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_SELF_MANAGE,
)


@get(
    "/{alias_id:int}",
    responses=responses(404, 422, 503),
    operation_id="creator_alias_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_creator_alias(alias_id: FromPath[int], accounts: Accounts, caller: CurrentCaller) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(alias_id, context=caller.context())
    if alias is None:
        raise CreatorAliasNotFoundError(alias_id)
    return CreatorAliasDetail.from_domain(alias)


@get(
    "/lookup",
    responses=responses(404, 422, 503),
    operation_id="creator_alias_lookup",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def look_up_creator_alias(
    name: Annotated[str, QueryParameter(min_length=1, max_length=255)], accounts: Accounts, caller: CurrentCaller
) -> CreatorAliasDetail:
    """Find a creator credit by name, ignoring case and Unicode spelling differences."""
    alias = await accounts.get_creator_alias(name, context=caller.context())
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


@dataclass(frozen=True, slots=True)
class CreditedNameSummary:
    id: int
    name: str
    stats: CreatorStats


@get(
    "/summary",
    responses=responses(404, 422, 503),
    operation_id="creator_name_summary",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def creator_name_summary(
    name: Annotated[str, QueryParameter(min_length=1, max_length=255)],
    accounts: Accounts,
    build_queries: BuildQueries,
    caller: CurrentCaller,
) -> CreditedNameSummary:
    """Summarize approved catalogue builds that credit this name."""
    alias = await accounts.get_creator_alias(name, context=caller.context())
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    stats = await build_queries.creator_stats(BuildListFilters(creator_name_id=alias.id), context=caller.context())
    return CreditedNameSummary(alias.id, alias.name, stats)


@post(
    "/{alias_id:int}/claims",
    operation_id="creator_credit_claim",
    response_headers={"Cache-Control": "private, no-store"},
    security=[WEB_WRITE, DEVICE],
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    opt=contract(cli=transport_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def claim_credits(
    alias_id: FromPath[int],
    data: JSONBody[CreditClaimRequest],
    accounts: Accounts,
    caller: CurrentCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CreditClaimDetail:
    """Request attribution for selected published credits under this name."""
    name = await accounts.get_creator_alias(alias_id, context=caller.context())
    if name is None:
        raise CreatorAliasNotFoundError(alias_id)
    claim = await accounts.request_alias_claim(name.name, context=caller.context(), credit_ids=data.credit_ids)
    return CreditClaimDetail.from_claim(claim)


@get(
    "/",
    operation_id="credit_claim_queue",
    security=[WEB, DEVICE],
    guards=[requires(ACCOUNT_CLAIM_LIST)],
    responses=responses(400, 401, 403, 422, 503),
    opt=contract(cli=transport_only()),
)
async def claim_queue(
    accounts: Accounts,
    caller: CurrentCaller,
    offset: OffsetParam = None,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.OLDEST,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 20,
) -> Page[CreditClaimDetail]:
    """List pending credit claims oldest first."""
    page = await accounts.credit_claim_page(
        context=caller.context(),
        staff_queue=True,
        selector=resolve_selector(offset=offset, cursor=cursor, sort=sort),
        page_size=page_size,
    )
    return Page.from_domain(page, CreditClaimDetail.from_claim)


@post(
    "/{claim_id:int}/approve",
    operation_id="credit_claim_approve",
    status_code=200,
    security=[WEB_WRITE, DEVICE],
    guards=[requires(ACCOUNT_CLAIM_APPROVE)],
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    opt=contract(cli=transport_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def approve_claim(
    claim_id: FromPath[int],
    data: JSONBody[CreditClaimApproval],
    accounts: Accounts,
    caller: CurrentCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CreditClaimDetail:
    """Approve selected credits and close the request."""
    return CreditClaimDetail.from_claim(
        await accounts.approve_alias_claim(
            claim_id, context=caller.context(), credit_ids=data.credit_ids, reassign=data.reassign
        )
    )


@post(
    "/{claim_id:int}/reject",
    operation_id="credit_claim_reject",
    status_code=200,
    security=[WEB_WRITE, DEVICE],
    guards=[requires(ACCOUNT_CLAIM_REJECT)],
    responses=responses(401, 403, 404, 409, 503),
    opt=contract(cli=transport_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def reject_claim(
    claim_id: FromPath[int],
    accounts: Accounts,
    caller: CurrentCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CreditClaimDetail:
    """Close a claim without assigning its credits."""
    return CreditClaimDetail.from_claim(await accounts.reject_alias_claim(claim_id, context=caller.context()))


claims_router = Router(
    path="/claims",
    opt={"x-squid-private-response": True},
    tags=["creators"],
    dependencies={"accounts": Provide(get_accounts, sync_to_thread=False)},
    route_handlers=[claim_queue, approve_claim, reject_claim],
)

router = Router(
    dependencies={
        "accounts": Provide(get_accounts, sync_to_thread=False),
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
    },
    path="/creator-aliases",
    tags=["creators"],
    route_handlers=[get_creator_alias, look_up_creator_alias, creator_name_summary, claim_credits],
)
