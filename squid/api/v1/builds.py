"""Build read and write routes."""

import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

from litestar import Request, Response, Router, get, patch
from litestar.background_tasks import BackgroundTask
from litestar.di import NamedDependency, Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, FromQuery, HeaderParameter, JSONBody, QueryParameter
from litestar.response import File

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
from squid.api.i18n import locale_for_request
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
from squid.api.v1.browser_views import BrowserViews
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildStatusFilter
from squid.api.v1.schemas.submissions import FormManifestResponse, SavedBuildFormResponse
from squid.api.v1.search import build_hit_id, hydrate_builds
from squid.core.builds.application import (
    BUILD_SORT_FIELDS,
    BuildEditPatch,
    BuildListSort,
    BuildSortField,
    PublicBuildSummary,
)
from squid.core.builds.application.edits import BUILD_EDIT, build_target, patch_values
from squid.core.builds.application.queries import BuildListFilters
from squid.core.builds.domain import BuildCategory
from squid.core.builds.errors import (
    BuildNotFoundError,
    BuildRevisionMismatchError,
    BuildRevisionRequiredError,
)
from squid.core.builds.history import PublicBuildRevision
from squid.core.builds.models import Build
from squid.core.edits.domain import EditCommand, LatestRevision, Revision
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import (
    BUILD_SUBMISSION_CREATE,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.core.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort
from squid.core.submissions.application.build_answers import answers_from_build

UserWriter = CurrentCaller
_BUILD_ETAG = re.compile(r'^"build-(?P<build_id>[1-9][0-9]*)-r(?P<revision>[1-9][0-9]*)"$')


@patch(
    "/{build_id:int}",
    responses={
        **responses(400, 401, 403, 404, 409, 412, 422, 503),
        202: ResponseSpec(
            BuildDetail, description="The edit was submitted for review. Build-Edit-Vote contains the vote ID."
        ),
    },
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
    """Apply a permitted build edit or submit it for review."""
    caller.require_consented_account()
    result = await services.edits.execute(
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
    headers = {"ETag": build_etag(build)}
    if result.outcome.review_vote_id is not None:
        headers["Build-Edit-Vote"] = str(result.outcome.review_vote_id)
        return Response(BuildDetail.from_domain(build), status_code=202, headers=headers)
    return Response(BuildDetail.from_domain(build), headers=headers)


@get(
    "/{build_id:int}",
    responses=responses(
        404,
        422,
        503,
        describe={404: "No approved build with this identifier. A pending build answers 404 as well."},
    ),
    operation_id="builds_get",
    response_headers={"Cache-Control": "private, no-store"},
    security=[ANONYMOUS, WEB],
    opt=contract(cli=transport_only()),
)
async def get_build(
    request: Request, build_id: FromPath[int], build_queries: BuildQueries, services: Services, caller: CurrentCaller
) -> Response[BuildDetail]:
    """Return one approved public build.

    Pending and rejected builds return 404, as do missing builds. This endpoint does
    not expose unpublished submissions, even to callers who can view them elsewhere.
    """
    build = await build_queries.get_public(build_id)
    detail = await BrowserViews(services, caller).build(build, request)
    return Response(detail, headers={"ETag": build_etag(build)})


@get(
    "/{build_id:int}/history",
    responses=responses(404, 422, 503),
    security=[ANONYMOUS],
    operation_id="builds_history_list",
    opt=contract(cli=transport_only()),
)
async def get_build_history(
    request: Request,
    build_id: FromPath[int],
    build_queries: BuildQueries,
    services: Services,
    offset: OffsetParam = None,
    page_size: PageSizeParam = 20,
) -> Page[PublicBuildRevision]:
    """List published revisions newest first, with changes to public build fields."""
    manifest = await services.submission_forms.edit_manifest(locale=locale_for_request(request))
    labels = {field.id: field.label for category in manifest.categories for field in manifest.fields_for(category.code)}
    history = await build_queries.public_history(build_id, labels, offset=offset or 0, page_size=page_size)
    return Page.from_domain(history)


@get(
    "/{build_id:int}/form",
    responses=responses(401, 403, 404, 422, 503),
    guards=[requires(BUILD_SUBMISSION_EDIT)],
    security=[WEB],
    response_headers={"Cache-Control": "private, no-store"},
    operation_id="builds_edit_form_get",
    opt=contract(cli=browser_only()),
)
async def get_build_form(
    request: Request, build_id: FromPath[int], build_queries: BuildQueries, services: Services
) -> SavedBuildFormResponse:
    """Read an approved build in the moderator editor's form vocabulary."""
    build = await build_queries.get_public(build_id)
    manifest = await services.submission_forms.edit_manifest(locale=locale_for_request(request))
    saved = answers_from_build(build)
    return SavedBuildFormResponse.model_validate(
        {
            "revision": build.revision,
            "category": build.category.value.lower(),
            "manifest": FormManifestResponse.from_domain(manifest),
            "answers": saved.answers,
            "unresolved": saved.unresolved,
        }
    )


@get(
    "/{build_id:int}/media/{upload_id:uuid}/content",
    responses=responses(404, 503),
    operation_id="build_media_content_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    media_type="application/octet-stream",
)
async def get_build_media(build_id: FromPath[int], upload_id: FromPath[UUID], services: Services) -> File:
    """Serve a retained media output while its build is approved."""
    media = services.published_build_media
    outputs = await media.outputs(build_id, upload_id=upload_id) if media else []
    if media is None or not outputs:
        raise BuildNotFoundError(build_id)
    artifact = outputs[0]
    directory = Path(tempfile.mkdtemp(prefix="squid-build-media-"))
    path = directory / "content"
    try:
        downloaded = await media.artifacts.get_path(artifact.object_key, path, max_bytes=artifact.byte_size)
    except BaseException:
        shutil.rmtree(directory)
        raise
    if downloaded is None or downloaded.byte_size != artifact.byte_size or downloaded.sha256 != artifact.sha256:
        shutil.rmtree(directory)
        raise BuildNotFoundError(build_id)
    return File(
        path,
        media_type=artifact.content_type,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(shutil.rmtree, directory),
    )


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
    category: FromQuery[BuildCategory | None] = None,
    version: Annotated[int | None, QueryParameter(ge=1)] = None,
    tag: FromQuery[list[str] | None] = None,
    creator_id: FromQuery[UUID | None] = None,
    submitted_by: FromQuery[Literal["me"] | None] = None,
    sort: FromQuery[Literal["newest", "oldest", "updated", "fastest_open", "smallest_volume", "title"] | None] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[PublicBuildSummary]:
    """Search approved builds, or list builds with a selected moderation status.

    Supplying q selects indexed search within the selected status. Search
    uses offset pagination. Without q, results come from the build repository;
    unpublished statuses require build.submission.view_pending permission.
    """
    orders = {
        "newest": "-submission_time",
        "oldest": "submission_time",
        "updated": "-edited_time",
        "fastest_open": "opening_time",
        "smallest_volume": "volume",
        "title": "title",
    }
    order = orders.get(sort, sort) if sort is not None else None
    filters = BuildListFilters(category, version, tuple(dict.fromkeys(tag or ())), creator_id)
    submitter_id = caller.require_consented_account() if submitted_by is not None else None
    effective = status or BuildStatusFilter.APPROVED
    if effective is not BuildStatusFilter.APPROVED and submitter_id is None:
        await _require_pending_view(permissions, caller)
    if q is not None:
        # Relevance order has no identifier sequence to anchor to, so ranked pages are offset-only.
        selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id, keyset_allowed=False)
        result = await search_service.search(
            SearchRequest(
                query=q,
                scope=SearchScope.BUILDS,
                mode=SearchMode.LEXICAL,
                page_size=page_size,
                offset=selector.offset,
                sort=SearchSort.parse(
                    {
                        "-submission_time": "-created_at",
                        "submission_time": "created_at",
                        "-edited_time": "-updated_at",
                    }.get(order, order)
                    if order is not None
                    else None
                ),
                visible_statuses=frozenset({effective.value}),
                catalogue_filters=filters,
                submitter_account_id=submitter_id,
            )
        )
        summaries = await hydrate_builds(
            build_queries, result.hits, status=effective.to_domain(), submitter_id=submitter_id
        )
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

    sort_field, descending = parse_page_sort(order, allowed=BUILD_SORT_FIELDS, default="-id")
    selector = resolve_selector(
        offset=offset, after_id=after_id, before_id=before_id, keyset_allowed=sort_field == "id"
    )
    page = await build_queries.list_page(
        statuses=frozenset({effective.to_domain()}),
        submitter_account_id=submitter_id,
        filters=filters,
        sort=BuildListSort(field=cast(BuildSortField, sort_field), descending=descending),
        selector=selector,
        page_size=page_size,
    )
    return Page[PublicBuildSummary].from_domain(page, PublicBuildSummary.from_build)


async def _require_pending_view(permissions: PermissionService, caller: Caller) -> None:
    """Require permission to view unpublished submissions, including credential restrictions."""
    await caller.require(permissions, BUILD_SUBMISSION_VIEW_PENDING)


def build_etag(build: Build) -> str:
    """Return the strong validator for one persisted build revision."""
    # ORM identity columns may still be None before flush.
    if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
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
    route_handlers=[edit_build, get_build, get_build_form, get_build_history, get_build_media, list_builds],
)
