"""Authenticated self-account routes."""

from typing import Annotated
from uuid import UUID

from litestar import Response, Router, delete, get, patch, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import HeaderParameter, JSONBody, SkipValidation

from squid.accounts.application import AccountService
from squid.accounts.application.edits import (
    PROFILE_CLEAR,
    PROFILE_EDIT,
    UNLINK_EDIT,
    VISIBILITY_EDIT,
    ProfileValues,
    account_target,
)
from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.accounts.errors import AccountIdentityNotFoundError, StaleConsentNoticeError
from squid.api.contract import ANONYMOUS, DEVICE, WEB, WEB_WRITE, browser_only, cli_command, contract
from squid.api.dependencies import Accounts, BuildQueries, Services
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request, reserve_idempotent_request
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    resolve_selector,
)
from squid.api.revisions import RevisionRequiredError, expected_revision, revision_etag
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
from squid.edits.domain import EditCommand, EditValues, Revision
from squid.permissions.domain.catalogue import (
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
    ACCOUNT_PROFILE_MODERATE,
    ACCOUNT_SELF_MANAGE,
    ACCOUNT_SELF_READ,
)

UserCaller = NamedDependency[SkipValidation[Caller]]
RefreshCaller = NamedDependency[SkipValidation[Caller]]
ManageCaller = NamedDependency[SkipValidation[Caller]]
_ALL_STATUSES = frozenset(Status)


@get(
    "/",
    responses=responses(401, 403, 404, 503),
    operation_id="account_get",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"caller": Provide(requires(ACCOUNT_SELF_READ))},
)
async def get_me(accounts: Accounts, caller: UserCaller) -> Response[UserMe]:
    """Return the caller's account, linked identities, and profile.

    An account ID is required; the caller does not need a Discord identity.
    """
    response_headers: dict[str, str] = {}
    if caller.account_id is None:
        raise AuthenticationError
    result = await _render_me(accounts, caller.account_id, consent_pending=caller.consent_pending)
    response_headers["ETag"] = revision_etag(Revision(account_target(caller.account_id), result.revision))
    return Response(content=result, headers=response_headers, status_code=200)


async def _render_me(accounts: AccountService, account_id: int, *, consent_pending: bool) -> UserMe:
    """Compose the self view from the account and its profile."""
    snapshot = await accounts.editor_snapshot(account_id)
    return UserMe.from_domain(snapshot.account, snapshot.profile, consent_pending=consent_pending)


@post(
    "/consent",
    responses=responses(401, 403, 404, 409, 503),
    operation_id="account_consent_grant",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
    },
)
async def grant_consent(
    accounts: Accounts,
    caller: UserCaller,
    data: JSONBody[ConsentGrantRequest | None] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> UserMe:
    """Accept the current privacy notice for future writes.

    If a notice version is supplied, it must match the current version. This lets
    clients detect that the displayed notice has become outdated.
    """
    if caller.account_id is None:
        raise AuthenticationError
    if data is not None and data.version is not None and (data.version != CURRENT_CONSENT_VERSION):
        raise StaleConsentNoticeError(offered=data.version, current=CURRENT_CONSENT_VERSION)
    await accounts.grant_current_consent(caller.account_id)
    return await _render_me(accounts, caller.account_id, consent_pending=False)


@patch(
    "/profile",
    responses=responses(400, 401, 403, 404, 412, 422, 503),
    operation_id="account_profile_update",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def update_profile(
    data: JSONBody[ProfileUpdateRequest],
    accounts: Accounts,
    caller: ManageCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[ProfileDetail]:
    """Edit the caller's own public profile.

    Partial: an omitted field is left alone and an explicit `null` clears it, so a client that
    only knows about some fields cannot wipe the ones it has never heard of.
    """
    response_headers: dict[str, str] = {}
    if caller.account_id is None:
        raise AuthenticationError
    await services.edits.execute(
        EditCommand(
            PROFILE_EDIT,
            (expected_revision(account_target(caller.account_id), if_match),),
            ProfileValues.patch(data.to_domain()),
            operation_id=operation_id,
        ),
        caller.to_actor(),
    )
    snapshot = await accounts.editor_snapshot(caller.account_id)
    response_headers["ETag"] = revision_etag(Revision(account_target(caller.account_id), snapshot.account.revision))
    return Response(
        content=ProfileDetail.from_domain(snapshot.profile, snapshot.account.identities),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/identities",
    responses=responses(401, 403, 404, 503),
    operation_id="account_identity_list",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"caller": Provide(requires(ACCOUNT_SELF_READ))},
)
async def list_identities(accounts: Accounts, caller: UserCaller) -> list[IdentityDetail]:
    """List every identity linked to the caller's account, hidden ones included.

    Unfiltered by visibility on purpose: you can only unhide what you can see listed.
    """
    if caller.account_id is None:
        raise AuthenticationError
    identities = await accounts.list_identities(caller.account_id)
    return [IdentityDetail.from_domain(identity) for identity in identities]


@put(
    "/identities/{identity_id:int}/visibility",
    responses=responses(401, 403, 404, 412, 422, 503),
    operation_id="account_identity_visibility_set",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def set_identity_visibility(
    identity_id: int,
    data: JSONBody[IdentityVisibilityRequest],
    accounts: Accounts,
    caller: ManageCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[IdentityDetail]:
    """Publish or withhold one linked identity on the public creator profile."""
    response_headers: dict[str, str] = {}
    if caller.account_id is None:
        raise AuthenticationError
    await services.edits.execute(
        EditCommand(
            VISIBILITY_EDIT,
            (expected_revision(account_target(caller.account_id), if_match),),
            EditValues.of({"identity_id": identity_id, "is_public": data.public}),
            operation_id=operation_id,
        ),
        caller.to_actor(),
    )
    snapshot = await accounts.editor_snapshot(caller.account_id)
    response_headers["ETag"] = revision_etag(Revision(account_target(caller.account_id), snapshot.account.revision))
    identity = next((identity for identity in snapshot.account.identities if identity.id == identity_id), None)
    if identity is None:
        raise AccountIdentityNotFoundError(identity_id, account_id=caller.account_id)
    return Response(content=IdentityDetail.from_domain(identity), headers=response_headers, status_code=200)


@delete(
    "/identities/{identity_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 409, 412, 422, 503),
    operation_id="account_identity_unlink",
    opt=contract(security=[WEB_WRITE, DEVICE], cli=cli_command("account.identity.unlink", interaction="direct")),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def unlink_identity(
    identity_id: int,
    caller: ManageCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Unlink one identity from the caller's account.

    Removing the last identity returns 409 so the account retains a sign-in method.
    Existing creator credits remain attached to the account.
    """
    if caller.account_id is None:
        raise AuthenticationError
    expected = expected_revision(account_target(caller.account_id), if_match)
    result = await services.edits.execute(
        EditCommand(UNLINK_EDIT, (expected,), EditValues.of({"identity_id": identity_id}), operation_id=operation_id),
        caller.to_actor(),
    )
    return Response(content=None, status_code=204, headers={"ETag": revision_etag(result.outcome.revisions[0])})


@post(
    "/merge-code",
    responses=responses(401, 403, 404, 503),
    operation_id="account_merge_code_create",
    opt=contract(security=[WEB_WRITE, DEVICE], cli=cli_command("account.merge.code", interaction="direct")),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
    },
)
async def create_merge_code(
    accounts: Accounts, caller: ManageCaller, *, _reservation: NamedDependency[SkipValidation[None]]
) -> MergeCodeDetail:
    """Create a code allowing another account to absorb this account.

    Create the code while signed in to the account being given up, then redeem it
    as the account being kept. Completing the merge permanently redirects the old
    creator ID. The code is shown on creation and replaces any previous code.
    """
    if caller.account_id is None:
        raise AuthenticationError
    code, ticket = await accounts.create_merge_code(caller.account_id)
    return MergeCodeDetail(code=code, expires_at=ticket.expires_at)


@post(
    "/merge/preview",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="account_merge_preview",
    opt=contract(security=[WEB_WRITE, DEVICE], cli=cli_command("account.merge.preview", interaction="direct")),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
    },
)
async def preview_merge(
    data: JSONBody[MergeRequest],
    accounts: Accounts,
    caller: ManageCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> MergePreviewDetail:
    """Describe what redeeming a merge code would move, without spending it.

    A merge cannot be undone, so this exists to be shown before the irreversible call.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return MergePreviewDetail.from_domain(await accounts.preview_merge(caller.account_id, data.code))


@post(
    "/merge",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="account_merge_complete",
    opt=contract(security=[WEB_WRITE, DEVICE], cli=cli_command("account.merge.complete", interaction="direct")),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "caller": Provide(requires(ACCOUNT_SELF_MANAGE)),
    },
)
async def complete_merge(
    data: JSONBody[MergeRequest],
    accounts: Accounts,
    caller: ManageCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> AccountMergeDetail:
    """Absorb the account that minted this code into the caller's account.

    Irreversible: the absorbed account's creator id becomes a permanent redirect to the caller's.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return AccountMergeDetail.from_domain(await accounts.complete_merge(caller.account_id, data.code))


@post(
    "/minecraft/refresh",
    responses=responses(401, 403, 404, 409, 503),
    operation_id="account_minecraft_refresh",
    opt=contract(security=[ANONYMOUS], cli=cli_command("account.refresh", interaction="direct")),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "caller": Provide(requires(ACCOUNT_IDENTITY_REFRESH)),
    },
)
async def refresh_minecraft_identity(
    accounts: Accounts, caller: RefreshCaller, *, _reservation: NamedDependency[SkipValidation[None]]
) -> MinecraftIdentityRefresh:
    """Re-read the caller's linked Minecraft name and reconcile the creator credit.

    Rate limited and idempotency-gated because it reaches Mojang on every call.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return MinecraftIdentityRefresh.from_domain(await accounts.refresh_java_identity(caller.account_id))


@post(
    "/{account_id:int}/minecraft/refresh",
    responses=responses(401, 403, 404, 409, 503),
    operation_id="account_minecraft_refresh_for",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_permission": Provide(requires(ACCOUNT_IDENTITY_REFRESH_ANY)),
        "_reservation": Provide(reserve_idempotent_request),
    },
)
async def refresh_minecraft_identity_for(
    account_id: int,
    accounts: Accounts,
    *,
    _permission: NamedDependency[SkipValidation[Caller]],
    _reservation: NamedDependency[SkipValidation[None]],
) -> MinecraftIdentityRefresh:
    """Re-read another account's linked Minecraft name, for staff resolving a stale credit."""
    return MinecraftIdentityRefresh.from_domain(await accounts.refresh_java_identity(account_id))


@get(
    "/{account_id:int}/profile",
    responses=responses(401, 403, 404, 503),
    operation_id="account_profile_review",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"_permission": Provide(requires(ACCOUNT_PROFILE_MODERATE))},
)
async def review_profile(
    account_id: int, accounts: Accounts, *, _permission: NamedDependency[SkipValidation[Caller]]
) -> Response[ProfileDetail]:
    """Read a profile and its revision for staff moderation."""
    response_headers: dict[str, str] = {}
    snapshot = await accounts.editor_snapshot(account_id)
    response_headers["ETag"] = revision_etag(Revision(account_target(account_id), snapshot.account.revision))
    return Response(
        content=ProfileDetail.from_domain(snapshot.profile, snapshot.account.identities),
        headers=response_headers,
        status_code=200,
    )


@delete(
    "/{account_id:int}/profile",
    responses=responses(401, 403, 404, 412, 422, 428, 503),
    operation_id="account_profile_clear",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_PROFILE_MODERATE)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def clear_profile(
    account_id: int,
    accounts: Accounts,
    caller: NamedDependency[SkipValidation[Caller]],
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[ProfileDetail]:
    """Clear profile content at the revision staff reviewed, retaining moderation attribution."""
    response_headers: dict[str, str] = {}
    if if_match is None:
        raise RevisionRequiredError
    await services.edits.execute(
        EditCommand(
            PROFILE_CLEAR,
            (expected_revision(account_target(account_id), if_match),),
            EditValues.of({}),
            operation_id=operation_id,
        ),
        caller.to_actor(),
    )
    snapshot = await accounts.editor_snapshot(account_id)
    response_headers["ETag"] = revision_etag(Revision(account_target(account_id), snapshot.account.revision))
    return Response(
        content=ProfileDetail.from_domain(snapshot.profile, snapshot.account.identities),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/builds",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="account_builds_list",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"caller": Provide(requires(ACCOUNT_SELF_READ))},
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
        statuses=statuses, submitter_account_id=caller.account_id, selector=selector, page_size=page_size
    )
    return Page[BuildSummary].from_domain(page, BuildSummary.from_domain)


router = Router(
    path="/users/me",
    tags=["users"],
    route_handlers=[
        get_me,
        grant_consent,
        update_profile,
        list_identities,
        set_identity_visibility,
        unlink_identity,
        create_merge_code,
        preview_merge,
        complete_merge,
        refresh_minecraft_identity,
        list_my_builds,
    ],
)


accounts_router = Router(
    path="/accounts", tags=["users"], route_handlers=[refresh_minecraft_identity_for, review_profile, clear_profile]
)
