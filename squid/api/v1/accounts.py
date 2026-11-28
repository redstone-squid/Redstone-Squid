"""Account routes: the caller's own account, public profiles, and staff moderation."""

from typing import Annotated, override
from uuid import UUID

from advanced_alchemy.extensions.litestar import SQLAlchemyDTO
from advanced_alchemy.extensions.litestar.dto import SQLAlchemyDTOConfig
from litestar import Response, Router, delete, get, patch, post, put
from litestar.di import NamedDependency, Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import (
    FromPath,
    FromQuery,
    HeaderParameter,
    JSONBody,
    PathParameter,
    SkipValidation,
)
from litestar.typing import FieldDefinition

from squid.api.contract import (
    ANONYMOUS,
    DEVICE,
    WEB,
    WEB_WRITE,
    browser_only,
    cli_command,
    contract,
    transport_only,
)
from squid.api.dependencies import Accounts, BuildQueries, CurrentCaller, Services, get_accounts, get_build_queries
from squid.api.errors import responses
from squid.api.exports import FormatParam, export_contract, export_response
from squid.api.idempotency import edit_operation_id, reserve_edit_request, reserve_idempotent_request
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.revisions import EDIT_ETAG, RevisionRequiredError, expected_revision, revision_etag
from squid.api.security import Caller, requires
from squid.api.v1.browser_views import BrowserViews
from squid.api.v1.schemas.accounts import (
    AccountDetail,
    BuildIndexingRequest,
    IdentityDetail,
    IdentityVisibilityRequest,
    MergeCodeDetail,
    MergeRequest,
    MinecraftIdentityRefresh,
    ProfileEditorDetail,
    ProfileUpdateRequest,
)
from squid.api.v1.schemas.creators import CreditClaimDetail
from squid.core.accounts.application.edits import (
    PROFILE_CLEAR,
    PROFILE_EDIT,
    UNLINK_EDIT,
    VISIBILITY_EDIT,
    ProfileValues,
    account_target,
)
from squid.core.accounts.domain import AccountMerge, MergePreview, PublicCreatorProfile
from squid.core.accounts.errors import AccountIdentityNotFoundError, AccountNotFoundError
from squid.core.accounts.models import AccountIdentity
from squid.core.builds.application.queries import CreatorStats
from squid.core.builds.domain import BuildListFilters
from squid.core.edits.domain import EditCommand, EditValues, Revision
from squid.core.errors import AuthenticationError
from squid.core.exports import ExportFormat
from squid.core.pagination import ChronologicalSort
from squid.core.permissions.domain.catalogue import (
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_PROFILE_MODERATE,
    ACCOUNT_SELF_MANAGE,
    ACCOUNT_SELF_READ,
)
from squid.core.submissions.application.reviews import CreditedBuild


class LinkedIdentityDTO(SQLAlchemyDTO[AccountIdentity]):
    """Expose caller-owned identity details without account IDs or provider metadata."""

    __schema_name__ = "LinkedIdentityDetail"
    config = SQLAlchemyDTOConfig(include={"id", "provider", "subject", "display_name", "verified_at", "is_public"})


class MergePreviewDTO(DataclassDTO[MergePreview]):
    """Show the absorbed account's public ID and what the merge would move."""

    __schema_name__ = "MergePreviewDetail"
    config = DTOConfig(
        include={"absorbed_public_account_id", "alias_names", "identity_count", "build_count"},
        rename_fields={"absorbed_public_account_id": "absorbed_account_id"},
    )


class AccountMergeDTO(DataclassDTO[AccountMerge]):
    """Expose public redirects while withholding the internal merged account IDs."""

    __schema_name__ = "AccountMergeDetail"
    config = DTOConfig(
        include={"surviving_public_account_id", "redirected_public_account_id"},
        rename_fields={
            "surviving_public_account_id": "surviving_account_id",
            "redirected_public_account_id": "redirected_account_id",
        },
    )


class PublicAccountDTO(DataclassDTO[PublicCreatorProfile]):
    """Expose the service's visibility-filtered profile with the public API's field names."""

    __schema_name__ = "PublicAccountDetail"

    config = DTOConfig(
        rename_fields={"public_id": "id", "canonical_public_id": "canonical_id"},
        include={
            "public_id",
            "viewer",
            "canonical_public_id",
            "hidden",
            "display_name",
            "bio",
            "pronouns",
            "avatar_url",
            "joined_at",
            "aliases",
            "links",
            "identities",
        },
    )

    @classmethod
    @override
    def detect_nested_field(cls, field_definition: FieldDefinition) -> bool:
        # These service-owned leaf dataclasses already contain only public fields. Keeping
        # them native also avoids Litestar's process-global nested DTO schema-name counter.
        return False


AccountPath = Annotated[
    str,
    PathParameter(
        pattern=r"^(me|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
        description="A public account ID, or `me` for the caller's own account.",
    ),
]


async def resolve_account(account_id: AccountPath, accounts: Accounts, caller: CurrentCaller) -> int:
    """Resolve `me` or a public account ID in the path, following merge redirects."""
    if account_id == "me":
        if caller.account_id is None:
            raise AuthenticationError
        return caller.account_id
    return await accounts.resolve_public_id(UUID(account_id), context=caller.context())


TargetAccount = NamedDependency[SkipValidation[int]]
UserCaller = NamedDependency[SkipValidation[Caller]]
ManageCaller = NamedDependency[SkipValidation[Caller]]


@get(
    "/me",
    responses=responses(401, 403, 404, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_get",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def get_me(accounts: Accounts, caller: UserCaller) -> Response[AccountDetail]:
    """Return the caller's account, linked identities, and profile.

    An account ID is required; the caller does not need a Discord identity.
    """
    if caller.account_id is None:
        raise AuthenticationError
    snapshot = await accounts.editor_snapshot(caller.account_id, context=caller.context())
    content = AccountDetail.from_domain(snapshot.account, snapshot.profile)
    return Response(
        content, headers={"ETag": revision_etag(Revision(account_target(caller.account_id), content.revision))}
    )


@get(
    "/me/export",
    operation_id="account_export",
    security=[WEB],
    opt={**contract(cli=browser_only()), "responses": export_contract(archive=True)},
    responses=responses(401, 403, 422, 503),
    guards=[requires(ACCOUNT_SELF_READ)],
    media_type="application/octet-stream",
)
async def export_me(
    services: Services,
    caller: UserCaller,
    file_format: FormatParam = ExportFormat.JSON,
) -> Response[bytes]:
    """Download profile, identities, credits, claims, submissions, drafts, preferences and notice receipt.

    Sign in with the owning browser session. The JSON document or ZIP of CSV tables includes
    attachment references. Each download carries its schema version and generation time.
    """
    caller.browser_binding()
    download = await services.account_exports.download(caller.require_account_id(), file_format)
    return export_response("account", download)


@put(
    "/me/discord-build-indexing",
    responses=responses(401, 403, 404, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_build_indexing_set",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def set_build_indexing(
    accounts: Accounts,
    caller: UserCaller,
    data: JSONBody[BuildIndexingRequest],
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[AccountDetail]:
    """Set automatic indexing for the caller's builds in participating Discord channels."""
    await accounts.set_discord_build_indexing(enabled=data.enabled, context=caller.context())
    return await get_me.fn(accounts, caller)


@patch(
    "/me/profile",
    responses=responses(400, 401, 403, 404, 412, 422, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_profile_update",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
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
) -> Response[ProfileEditorDetail]:
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
        context=caller.context(),
    )
    snapshot = await accounts.editor_snapshot(caller.account_id, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(account_target(caller.account_id), snapshot.account.revision))
    return Response(
        content=ProfileEditorDetail.from_snapshot(snapshot),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/me/identities",
    responses=responses(401, 403, 404, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_identity_list",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def list_identities(
    accounts: Accounts,
    caller: UserCaller,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.OLDEST,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 50,
) -> Response[Page[IdentityDetail]]:
    """List every identity linked to the caller's account, hidden ones included.

    Unfiltered by visibility on purpose: you can only unhide what you can see listed.
    """
    if caller.account_id is None:
        raise AuthenticationError
    snapshot = await accounts.editor_snapshot(caller.account_id, context=caller.context())
    page = sort.page(
        snapshot.account.identities,
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
        identifier=lambda row: row.id,
        created_at=lambda row: row.created_at,
    )
    return Response(
        Page[IdentityDetail].from_domain(page, IdentityDetail.from_domain),
        headers={"ETag": revision_etag(Revision(account_target(caller.account_id), snapshot.account.revision))},
    )


@put(
    "/me/identities/{identity_id:int}/visibility",
    responses=responses(401, 403, 404, 412, 422, 503),
    operation_id="account_identity_visibility_set",
    return_dto=LinkedIdentityDTO,
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def set_identity_visibility(
    identity_id: FromPath[int],
    data: JSONBody[IdentityVisibilityRequest],
    accounts: Accounts,
    caller: ManageCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[AccountIdentity]:
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
        context=caller.context(),
    )
    snapshot = await accounts.editor_snapshot(caller.account_id, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(account_target(caller.account_id), snapshot.account.revision))
    identity = next((identity for identity in snapshot.account.identities if identity.id == identity_id), None)
    if identity is None:
        raise AccountIdentityNotFoundError(identity_id, account_id=caller.account_id)
    return Response(content=identity, headers=response_headers, status_code=200)


@delete(
    "/me/identities/{identity_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 409, 412, 422, 503),
    operation_id="account_identity_unlink",
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=cli_command("account.identity.unlink", interaction="direct")),
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def unlink_identity(
    identity_id: FromPath[int],
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
        context=caller.context(),
    )
    return Response(content=None, status_code=204, headers={"ETag": revision_etag(result.outcome.revisions[0])})


@post(
    "/me/merge-code",
    responses=responses(401, 403, 404, 503),
    operation_id="account_merge_code_create",
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=cli_command("account.merge.code", interaction="direct")),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def create_merge_code(
    accounts: Accounts, caller: ManageCaller, *, _reservation: NamedDependency[SkipValidation[None]]
) -> MergeCodeDetail:
    """Create a code allowing another account to absorb this account.

    Create the code while signed in to the account being given up, then redeem it
    as the account being kept. Completing the merge permanently redirects the old
    account ID. The code is shown on creation and replaces any previous code.
    """
    if caller.account_id is None:
        raise AuthenticationError
    code, ticket = await accounts.create_merge_code(context=caller.context())
    return MergeCodeDetail(code=code, expires_at=ticket.expires_at)


@post(
    "/me/merge/preview",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="account_merge_preview",
    return_dto=MergePreviewDTO,
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=cli_command("account.merge.preview", interaction="direct")),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def preview_merge(
    data: JSONBody[MergeRequest],
    accounts: Accounts,
    caller: ManageCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> MergePreview:
    """Describe what redeeming a merge code would move, without spending it.

    A merge cannot be undone, so this exists to be shown before the irreversible call.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return await accounts.preview_merge(data.code, context=caller.context())


@post(
    "/me/merge",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="account_merge_complete",
    return_dto=AccountMergeDTO,
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=cli_command("account.merge.complete", interaction="direct")),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def complete_merge(
    data: JSONBody[MergeRequest],
    accounts: Accounts,
    caller: ManageCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> AccountMerge:
    """Absorb the account that minted this code into the caller's account.

    Irreversible: the absorbed account's ID becomes a permanent redirect to the caller's.
    """
    if caller.account_id is None:
        raise AuthenticationError
    return await accounts.complete_merge(data.code, context=caller.context())


@get(
    "/{account_id:uuid}",
    responses=responses(404, 422, 503),
    operation_id="account_public_get",
    return_dto=PublicAccountDTO,
    security=[ANONYMOUS, WEB],
    opt=contract(cli=transport_only()),
)
async def get_public_account(
    account_id: FromPath[UUID], accounts: Accounts, services: Services, caller: CurrentCaller
) -> PublicCreatorProfile:
    """Return an account's public profile, following account-merge redirects.

    The account service applies profile and identity visibility before this route
    serializes the result. Your own ID returns the same public view; `/accounts/me` is the private one.
    """
    profile = await accounts.get_public_profile(account_id, context=caller.context())
    if profile is None:
        raise AccountNotFoundError(public_id=account_id)
    return await BrowserViews(services, caller).creator(profile)


@post(
    "/{account_id:str}/minecraft/refresh",
    responses=responses(400, 401, 403, 404, 409, 503),
    operation_id="account_minecraft_refresh",
    security=[ANONYMOUS],
    opt=contract(cli=cli_command("account.refresh", interaction="direct")),
    status_code=200,
    guards=[requires(ACCOUNT_IDENTITY_REFRESH)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def refresh_minecraft_identity(
    caller: NamedDependency[SkipValidation[Caller]],
    target: TargetAccount,
    accounts: Accounts,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> MinecraftIdentityRefresh:
    """Re-read an account's linked Minecraft name and reconcile its creator credit.

    Another account's name needs `account.identity.refresh_any`. Rate limited and
    idempotency-gated because it reaches Mojang on every call.
    """
    return MinecraftIdentityRefresh.from_domain(await accounts.refresh_java_identity(target, context=caller.context()))


@get(
    "/{account_id:str}/profile",
    responses=responses(400, 401, 403, 404, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_profile_get",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def get_profile(
    target: TargetAccount, accounts: Accounts, caller: NamedDependency[SkipValidation[Caller]]
) -> Response[ProfileEditorDetail]:
    """Read a profile and its revision, hidden fields included.

    Another account's profile needs `account.profile.moderate`.
    """
    response_headers: dict[str, str] = {}
    snapshot = await accounts.editor_snapshot(target, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(account_target(target), snapshot.account.revision))
    return Response(
        content=ProfileEditorDetail.from_snapshot(snapshot),
        headers=response_headers,
        status_code=200,
    )


@delete(
    "/{account_id:str}/profile",
    responses=responses(400, 401, 403, 404, 412, 422, 428, 503),
    response_headers=[EDIT_ETAG],
    operation_id="account_profile_clear",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_PROFILE_MODERATE)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def clear_profile(
    target: TargetAccount,
    accounts: Accounts,
    caller: NamedDependency[SkipValidation[Caller]],
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[ProfileEditorDetail]:
    """Clear profile content at the revision staff reviewed, retaining moderation attribution."""
    response_headers: dict[str, str] = {}
    if if_match is None:
        raise RevisionRequiredError
    await services.edits.execute(
        EditCommand(
            PROFILE_CLEAR,
            (expected_revision(account_target(target), if_match),),
            EditValues.of({}),
            operation_id=operation_id,
        ),
        context=caller.context(),
    )
    snapshot = await accounts.editor_snapshot(target, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(account_target(target), snapshot.account.revision))
    return Response(
        content=ProfileEditorDetail.from_snapshot(snapshot),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/{account_id:uuid}/creator-stats",
    operation_id="account_creator_stats",
    responses=responses(404, 503),
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    dependencies={"build_queries": Provide(get_build_queries, sync_to_thread=False)},
)
async def account_creator_stats(
    account_id: FromPath[UUID], accounts: Accounts, build_queries: BuildQueries, caller: CurrentCaller
) -> CreatorStats:
    """Summarize the builds publicly attributed to an account, following merge redirects."""
    await accounts.resolve_public_id(account_id, context=caller.context())
    return await build_queries.creator_stats(
        BuildListFilters(public_account_ids=(account_id,)), context=caller.context()
    )


@get(
    "/me/claims",
    operation_id="account_credit_claims",
    security=[WEB, DEVICE],
    guards=[requires(ACCOUNT_SELF_READ)],
    responses=responses(400, 401, 403, 422, 503),
    opt=contract(cli=transport_only()),
)
async def own_credit_claims(
    accounts: Accounts,
    caller: CurrentCaller,
    offset: OffsetParam = None,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.NEWEST,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 20,
) -> Page[CreditClaimDetail]:
    """Return the caller's credit claims and their outcomes."""
    page = await accounts.credit_claim_page(
        context=caller.context(),
        staff_queue=False,
        selector=resolve_selector(offset=offset, cursor=cursor, sort=sort),
        page_size=page_size,
    )
    return Page.from_domain(page, CreditClaimDetail.from_claim)


@get(
    "/me/builds",
    operation_id="account_credited_builds",
    security=[WEB],
    opt=contract(cli=browser_only()),
    responses=responses(401, 403, 422, 503),
    response_headers={"Cache-Control": "private, no-store"},
)
async def credited_builds(
    services: Services,
    caller: CurrentCaller,
    offset: OffsetParam = None,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.NEWEST,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 20,
) -> Page[CreditedBuild]:
    """List published builds credited to this account, including its private attributions."""
    if caller.kind != "account":
        raise AuthenticationError
    return Page.from_domain(
        await services.viewer_queries.portfolio(
            resolve_selector(offset=offset, cursor=cursor, sort=sort), page_size, context=caller.context()
        )
    )


router = Router(
    opt={"x-squid-private-response": True},
    dependencies={
        "accounts": Provide(get_accounts, sync_to_thread=False),
        "target": Provide(resolve_account),
    },
    path="/accounts",
    tags=["accounts"],
    route_handlers=[
        credited_builds,
        get_me,
        export_me,
        own_credit_claims,
        account_creator_stats,
        set_build_indexing,
        update_profile,
        list_identities,
        set_identity_visibility,
        unlink_identity,
        create_merge_code,
        preview_merge,
        complete_merge,
        get_public_account,
        refresh_minecraft_identity,
        get_profile,
        clear_profile,
    ],
)
