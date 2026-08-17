"""Authenticated self-account routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from squid.accounts.errors import AccountNotFoundError
from squid.api.contract import ANONYMOUS, WEB, WEB_WRITE, browser_only, cli_command, contract
from squid.api.dependencies import Accounts, BuildQueries
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    render_page,
    resolve_selector,
)
from squid.api.rate_limit import enforce_route_rate_limits
from squid.api.security import Caller, requires
from squid.api.v1.schemas.builds import BuildStatusFilter, BuildSummary
from squid.api.v1.schemas.me import MinecraftIdentityRefresh, UserMe
from squid.builds.domain import Status
from squid.core.errors import AuthenticationError
from squid.permissions.domain.catalogue import (
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
    ACCOUNT_SELF_READ,
)

router = APIRouter(prefix="/users/me", tags=["users"])
accounts_router = APIRouter(prefix="/accounts", tags=["users"])
UserCaller = Annotated[Caller, Depends(requires(ACCOUNT_SELF_READ))]
RefreshCaller = Annotated[Caller, Depends(requires(ACCOUNT_IDENTITY_REFRESH))]
_ALL_STATUSES = frozenset(Status)


@router.get(
    "",
    response_model=UserMe,
    responses=responses(401, 403, 404, 503),
    operation_id="account_get",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def get_me(accounts: Accounts, caller: UserCaller) -> UserMe:
    """Return the authenticated user's own linked account.

    Keyed on `account_id`, not `discord_id`: a CLI device and a Minecraft player both carry a
    perfectly good account and no Discord identity, and used to be refused their own account.
    """
    if caller.account_id is None:
        raise AuthenticationError
    account = await accounts.get_account_by_id(caller.account_id)
    if account is None:
        raise AccountNotFoundError(caller.account_id)
    return UserMe.from_domain(account, consent_pending=caller.consent_pending)


@router.post(
    "/consent",
    response_model=UserMe,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="account_consent_grant",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def grant_consent(accounts: Accounts, caller: UserCaller) -> UserMe:
    """Accept the current privacy notice for future writes."""
    if caller.account_id is None:
        raise AuthenticationError
    account = await accounts.grant_current_consent(caller.account_id)
    return UserMe.from_domain(account, consent_pending=False)


@router.post(
    "/minecraft/refresh",
    response_model=MinecraftIdentityRefresh,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="account_minecraft_refresh",
    openapi_extra=contract(security=[ANONYMOUS], cli=cli_command("account.refresh", interaction="direct")),
)
async def refresh_minecraft_identity(accounts: Accounts, caller: RefreshCaller) -> MinecraftIdentityRefresh:
    """Re-read the caller's linked Minecraft name and reconcile the creator credit.

    Rate limited and idempotency-gated because it reaches Mojang on every call.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return MinecraftIdentityRefresh.from_domain(await accounts.refresh_java_identity(caller.account_id))


@accounts_router.post(
    "/{account_id}/minecraft/refresh",
    response_model=MinecraftIdentityRefresh,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[
        Depends(requires(ACCOUNT_IDENTITY_REFRESH_ANY)),
        Depends(enforce_route_rate_limits),
        Depends(enforce_request_idempotency),
    ],
    operation_id="account_minecraft_refresh_for",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def refresh_minecraft_identity_for(account_id: int, accounts: Accounts) -> MinecraftIdentityRefresh:
    """Re-read another account's linked Minecraft name, for staff resolving a stale credit."""
    return MinecraftIdentityRefresh.from_domain(await accounts.refresh_java_identity(account_id))


@router.get(
    "/builds",
    response_model=Page[BuildSummary],
    responses=responses(400, 401, 403, 422, 503),
    operation_id="account_builds_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_my_builds(
    build_queries: BuildQueries,
    caller: UserCaller,
    status: BuildStatusFilter | None = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[BuildSummary]:
    """List the caller's own submissions, including the ones still awaiting review.

    This is the authoritative counterpart to `GET /v1/builds`: submitters need to see their own
    pending and denied builds, which the public search path deliberately cannot return.
    """
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    statuses = _ALL_STATUSES if status is None else frozenset({status.to_domain()})
    page = await build_queries.list_page(
        statuses=statuses,
        submitter_account_id=caller.account_id,
        selector=selector,
        page_size=page_size,
    )
    return render_page(page, BuildSummary.from_domain)
