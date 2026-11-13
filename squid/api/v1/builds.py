"""Build read and write routes."""

import re
from typing import Annotated, cast
from uuid import UUID

from litestar import Response, Router, get, patch
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, HeaderParameter, JSONBody, QueryParameter

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import (
    BuildQueries,
    CurrentCaller,
    Permissions,
    Search,
    Services,
    get_build_queries,
    get_permissions,
    get_search,
)
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_before_request
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageAnchor,
    PageSizeParam,
    parse_page_sort,
    resolve_selector,
)
from squid.api.security import Caller, requires
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildStatusFilter, BuildSummary
from squid.api.v1.search import PUBLIC_SEARCH_STATUSES, build_hit_id, hydrate_builds
from squid.builds.application import (
    BUILD_SORT_FIELDS,
    BuildEditPatch,
    BuildListSort,
    BuildSortField,
)
from squid.builds.application.edits import BUILD_EDIT, build_target, patch_values
from squid.builds.errors import (
    BuildNotFoundError,
    BuildRevisionMismatchError,
    BuildRevisionRequiredError,
)
from squid.builds.models import Build
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.edits.domain import EditCommand, LatestRevision, Revision
from squid.permissions.application import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_CREATE, BUILD_SUBMISSION_VIEW_PENDING
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort

UserWriter = CurrentCaller
_BUILD_ETAG = re.compile(r'^"build-(?P<build_id>[1-9][0-9]*)-r(?P<revision>[1-9][0-9]*)"$')


@patch(
    "/{build_id:int}",
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    before_request=reserve_edit_before_request,
    guards=[requires(BUILD_SUBMISSION_CREATE)],
    dependencies={"operation_id": Provide(edit_operation_id, sync_to_thread=False)},
    operation_id="builds_update",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
)
async def edit_build(
    build_id: FromPath[int],
    data: JSONBody[BuildPatch],
    services: Services,
    caller: UserWriter,
    operation_id: NamedDependency[UUID],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
) -> Response[BuildDetail]:
    """Edit an owned pending build, or any build with `build.submission.edit`."""
    caller.require_consented_account()
    await services.edits.execute(
        EditCommand(
            BUILD_EDIT,
            (
                LatestRevision(build_target(build_id))
                if if_match is None
                else Revision(build_target(build_id), _expected_revision(build_id, if_match)),
            ),
            patch_values(BuildEditPatch.from_attributes(data.edit_attributes())),
            operation_id,
        ),
        caller.to_actor(),
    )
    build = await services.builds.get(build_id)
    if build is None:
        raise BuildNotFoundError(build_id)
    return Response(BuildDetail.from_domain(build), headers={"ETag": build_etag(build)})


@get(
    "/{build_id:int}",
    responses=responses(
        404,
        422,
        503,
        describe={404: "No confirmed build with this identifier. A pending build answers 404 as well."},
    ),
    operation_id="builds_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_build(build_id: FromPath[int], build_queries: BuildQueries) -> Response[BuildDetail]:
    """Return one confirmed public build.

    Pending and denied builds return 404, as do missing builds. This endpoint does
    not expose unpublished submissions, even to callers who can view them elsewhere.
    """
    build = await build_queries.get_public(build_id)
    return Response(BuildDetail.from_domain(build), headers={"ETag": build_etag(build)})


@get(
    "/",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="builds_list",
    security=[ANONYMOUS, SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only()),
)
async def list_builds(
    build_queries: BuildQueries,
    search_service: Search,
    permissions: Permissions,
    caller: CurrentCaller,
    q: Annotated[str | None, QueryParameter(max_length=1_000)] = None,
    status: FromQuery[BuildStatusFilter | None] = None,
    sort: Annotated[str | None, QueryParameter(max_length=80)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[BuildSummary]:
    """Search confirmed builds, or list builds with a selected moderation status.

    Supplying q selects indexed search and cannot be combined with status. Search
    uses offset pagination. Without q, results come from the build repository;
    unpublished statuses require build.submission.view_pending permission.
    """
    if q is not None:
        if status is not None:
            msg = "status cannot be combined with q"
            raise ValidationError(msg, public_context={"field": "status"})
        # Relevance order has no identifier sequence to anchor to, so ranked pages are offset-only.
        selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id, keyset_allowed=False)
        result = await search_service.search(
            SearchRequest(
                query=q,
                scope=SearchScope.BUILDS,
                mode=SearchMode.LEXICAL,
                page_size=page_size,
                offset=selector.offset,
                sort=SearchSort.parse(sort),
                visible_statuses=PUBLIC_SEARCH_STATUSES,
            )
        )
        summaries = await hydrate_builds(build_queries, result.hits)
        return Page(
            # A page can come back short of `total` when the projection outlived a build; the count
            # is the search backend's, and hydrate_builds has already logged the drift.
            items=[
                summary for hit in result.hits if (summary := summaries.get(build_hit_id(hit.source_id))) is not None
            ],
            total=result.total,
            next=PageAnchor.from_domain(result.next) if result.next is not None else None,
            prev=PageAnchor.from_domain(result.prev) if result.prev is not None else None,
        )

    sort_field, descending = parse_page_sort(sort, allowed=BUILD_SORT_FIELDS, default="-id")
    selector = resolve_selector(
        offset=offset, after_id=after_id, before_id=before_id, keyset_allowed=sort_field == "id"
    )
    effective = status or BuildStatusFilter.CONFIRMED
    if effective is not BuildStatusFilter.CONFIRMED:
        await _require_pending_view(permissions, caller)
    page = await build_queries.list_page(
        statuses=frozenset({effective.to_domain()}),
        sort=BuildListSort(field=cast(BuildSortField, sort_field), descending=descending),
        selector=selector,
        page_size=page_size,
    )
    return Page[BuildSummary].from_domain(page, BuildSummary.from_domain)


async def _require_pending_view(permissions: PermissionService, caller: Caller) -> None:
    """Require permission to view unpublished submissions, including credential restrictions."""
    if not await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING):
        raise AuthenticationError if caller.kind == "anonymous" else AuthorizationError


def build_etag(build: Build) -> str:
    """Return the strong validator for one persisted build revision."""
    if build.id is None:
        msg = "Cannot create an ETag for an unpersisted build."
        raise ValueError(msg)
    return f'"build-{build.id}-r{build.revision}"'


def _expected_revision(build_id: int, if_match: str | None) -> int:
    """Read the revision from a single strong build ETag.

    Missing If-Match raises BuildRevisionRequiredError. A malformed tag or a tag
    for another build raises BuildRevisionMismatchError.
    """
    if if_match is None:
        raise BuildRevisionRequiredError(build_id)
    match = _BUILD_ETAG.fullmatch(if_match.strip())
    if match is None or int(match.group("build_id")) != build_id:
        raise BuildRevisionMismatchError(build_id, expected_revision=None)
    return int(match.group("revision"))


router = Router(
    dependencies={
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
        "permissions": Provide(get_permissions, sync_to_thread=False),
        "search_service": Provide(get_search, sync_to_thread=False),
    },
    path="/builds",
    tags=["builds"],
    route_handlers=[edit_build, get_build, list_builds],
)
