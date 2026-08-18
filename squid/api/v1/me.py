"""Authenticated self-account routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.accounts.errors import AccountNotFoundError, StaleConsentNoticeError
from squid.api.contract import ANONYMOUS, DEVICE, WEB, WEB_WRITE, browser_only, cli_command, contract
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
from squid.api.v1.schemas.consent import ConsentGrantRequest
from squid.api.v1.schemas.me import (
    AccountMergeDetail,
    IdentityDetail,
    IdentityVisibilityRequest,
    MergeCodeDetail,
    MergePreviewDetail,
    MergeRequest,
    MinecraftIdentityRefresh,
    ProfileDetail,
    ProfileUpdateRequest,
    UserMe,
)
from squid.builds.domain import Status
from squid.core.errors import AuthenticationError
from squid.permissions.domain.catalogue import (
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
    ACCOUNT_PROFILE_MODERATE,
    ACCOUNT_SELF_MANAGE,
    ACCOUNT_SELF_READ,
)

router = APIRouter(prefix="/users/me", tags=["users"])
accounts_router = APIRouter(prefix="/accounts", tags=["users"])
UserCaller = Annotated[Caller, Depends(requires(ACCOUNT_SELF_READ))]
RefreshCaller = Annotated[Caller, Depends(requires(ACCOUNT_IDENTITY_REFRESH))]
ManageCaller = Annotated[Caller, Depends(requires(ACCOUNT_SELF_MANAGE))]
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
    return await _render_me(accounts, caller.account_id, consent_pending=caller.consent_pending)


async def _render_me(accounts: AccountService, account_id: int, *, consent_pending: bool) -> UserMe:
    """Compose the self view from the account and its profile."""
    account = await accounts.get_account_by_id(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return UserMe.from_domain(account, await accounts.get_profile(account_id), consent_pending=consent_pending)


@router.post(
    "/consent",
    response_model=UserMe,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="account_consent_grant",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def grant_consent(
    accounts: Accounts,
    caller: UserCaller,
    body: ConsentGrantRequest | None = None,
) -> UserMe:
    """Accept the current privacy notice for future writes.

    A client that names the version it displayed is held to it: consent recorded against text the
    user never saw is the failure that versioning the notice exists to prevent.
    """
    if caller.account_id is None:
        raise AuthenticationError
    if body is not None and body.version is not None and body.version != CURRENT_CONSENT_VERSION:
        raise StaleConsentNoticeError(offered=body.version, current=CURRENT_CONSENT_VERSION)
    await accounts.grant_current_consent(caller.account_id)
    return await _render_me(accounts, caller.account_id, consent_pending=False)


@router.patch(
    "/profile",
    response_model=ProfileDetail,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="account_profile_update",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def update_profile(
    body: ProfileUpdateRequest,
    accounts: Accounts,
    caller: ManageCaller,
) -> ProfileDetail:
    """Edit the caller's own public profile.

    Partial: an omitted field is left alone and an explicit `null` clears it, so a client that
    only knows about some fields cannot wipe the ones it has never heard of.
    """
    if caller.account_id is None:
        raise AuthenticationError
    profile = await accounts.update_profile(caller.account_id, body.to_domain())
    return ProfileDetail.from_domain(profile, await accounts.list_identities(caller.account_id))


@router.get(
    "/identities",
    response_model=list[IdentityDetail],
    responses=responses(401, 403, 404, 503),
    operation_id="account_identity_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_identities(accounts: Accounts, caller: UserCaller) -> list[IdentityDetail]:
    """List every identity linked to the caller's account, hidden ones included.

    Unfiltered by visibility on purpose: you can only unhide what you can see listed.
    """
    if caller.account_id is None:
        raise AuthenticationError
    identities = await accounts.list_identities(caller.account_id)
    return [IdentityDetail.from_domain(identity) for identity in identities]


@router.put(
    "/identities/{identity_id}/visibility",
    response_model=IdentityDetail,
    responses=responses(401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="account_identity_visibility_set",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def set_identity_visibility(
    identity_id: int,
    body: IdentityVisibilityRequest,
    accounts: Accounts,
    caller: ManageCaller,
) -> IdentityDetail:
    """Publish or withhold one linked identity on the public creator profile."""
    if caller.account_id is None:
        raise AuthenticationError
    identity = await accounts.set_identity_visibility(caller.account_id, identity_id, is_public=body.public)
    return IdentityDetail.from_domain(identity)


@router.delete(
    "/identities/{identity_id}",
    response_model=IdentityDetail,
    responses=responses(401, 403, 404, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="account_identity_unlink",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE],
        cli=cli_command("account.identity.unlink", interaction="direct"),
    ),
)
async def unlink_identity(identity_id: int, accounts: Accounts, caller: ManageCaller) -> IdentityDetail:
    """Unlink one identity from the caller's account.

    Refuses the last one with 409: every sign-in path resolves an account from a provider
    subject, so an account with no identities is one nobody can reach again. Creator credit is
    untouched — attribution is a fact about a build, not about how its author signs in.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return IdentityDetail.from_domain(await accounts.unlink_identity(caller.account_id, identity_id))


@router.post(
    "/merge-code",
    response_model=MergeCodeDetail,
    responses=responses(401, 403, 404, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="account_merge_code_create",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE],
        cli=cli_command("account.merge.code", interaction="direct"),
    ),
)
async def create_merge_code(accounts: Accounts, caller: ManageCaller) -> MergeCodeDetail:
    """Offer this account up to be absorbed by another one you hold.

    Run this as the account you are giving up: it loses its public creator id to a permanent
    redirect, so minting the code is that side's consent. Redeem it as the account you are
    keeping. The code is shown once and replaces any previous one.
    """
    if caller.account_id is None:
        raise AuthenticationError
    code, ticket = await accounts.create_merge_code(caller.account_id)
    return MergeCodeDetail(code=code, expires_at=ticket.expires_at)


@router.post(
    "/merge/preview",
    response_model=MergePreviewDetail,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="account_merge_preview",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE],
        cli=cli_command("account.merge.preview", interaction="direct"),
    ),
)
async def preview_merge(body: MergeRequest, accounts: Accounts, caller: ManageCaller) -> MergePreviewDetail:
    """Describe what redeeming a merge code would move, without spending it.

    A merge cannot be undone, so this exists to be shown before the irreversible call.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return MergePreviewDetail.from_domain(await accounts.preview_merge(caller.account_id, body.code))


@router.post(
    "/merge",
    response_model=AccountMergeDetail,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="account_merge_complete",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE],
        cli=cli_command("account.merge.complete", interaction="direct"),
    ),
)
async def complete_merge(body: MergeRequest, accounts: Accounts, caller: ManageCaller) -> AccountMergeDetail:
    """Absorb the account that minted this code into the caller's account.

    Irreversible: the absorbed account's creator id becomes a permanent redirect to the caller's.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return AccountMergeDetail.from_domain(await accounts.complete_merge(caller.account_id, body.code))


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


@accounts_router.delete(
    "/{account_id}/profile",
    response_model=ProfileDetail,
    responses=responses(401, 403, 404, 422, 503),
    dependencies=[
        Depends(requires(ACCOUNT_PROFILE_MODERATE)),
        Depends(enforce_request_idempotency),
    ],
    operation_id="account_profile_clear",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def clear_profile(account_id: int, accounts: Accounts) -> ProfileDetail:
    """Reset another account's profile to empty, for staff handling abuse.

    Deliberately leaves the profile visible: `hidden` belongs to its owner, and a takedown that
    also flipped it would take that decision away from them.
    """
    profile = await accounts.clear_profile(account_id)
    return ProfileDetail.from_domain(profile, await accounts.list_identities(account_id))


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
