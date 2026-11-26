"""Build read and write routes."""

import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from litestar import Request, Response, Router, get, patch
from litestar.di import NamedDependency, Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, FromQuery, HeaderParameter, JSONBody, QueryParameter
from litestar.response import File

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import (
    BuildQueries,
    CurrentCaller,
    Permissions,
    Services,
    get_build_queries,
    get_permissions,
)
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.idempotency import edit_operation_id, reserve_edit_before_request
from squid.api.media_files import media_file
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    resolve_selector,
)
from squid.api.security import Caller, requires
from squid.api.v1.browser_views import BrowserViews
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildStatusFilter
from squid.api.v1.schemas.submissions import FormManifestResponse, SavedBuildFormResponse
from squid.core.actors import ActingContext
from squid.core.builds.application import (
    BuildEditPatch,
    BuildListSort,
    PublicBuildSummary,
)
from squid.core.builds.application.edits import BUILD_EDIT, build_target, patch_values
from squid.core.builds.domain import BuildCategory, BuildListFilters, BuildStatus
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
    context = caller.context()
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
        context=context,
    )
    build = await services.builds.get(build_id, context=context)
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
    build = await build_queries.get_public(build_id, context=caller.context())
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
    caller: CurrentCaller,
    offset: OffsetParam = None,
    page_size: PageSizeParam = 20,
) -> Page[PublicBuildRevision]:
    """List published revisions newest first, with changes to public build fields."""
    manifest = await services.submission_forms.edit_manifest(
        locale=locale_for_request(request), context=caller.context()
    )
    labels = {field.id: field.label for category in manifest.categories for field in manifest.fields_for(category.code)}
    history = await build_queries.public_history(
        build_id, labels, offset=offset or 0, page_size=page_size, context=caller.context()
    )
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
    request: Request, build_id: FromPath[int], build_queries: BuildQueries, services: Services, caller: CurrentCaller
) -> SavedBuildFormResponse:
    """Read an approved build in the moderator editor's form vocabulary."""
    build = await build_queries.get_public(build_id, context=caller.context())
    manifest = await services.submission_forms.edit_manifest(
        locale=locale_for_request(request), context=caller.context()
    )
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
    outputs = await media.outputs(build_id, upload_id=upload_id, context=ActingContext.current()) if media else []
    if media is None or not outputs:
        raise BuildNotFoundError(build_id)
    artifact = outputs[0]

    async def fetch(path: Path) -> str:
        downloaded = await media.artifacts.get_path(artifact.object_key, path, max_bytes=artifact.byte_size)
        if downloaded is None or downloaded.byte_size != artifact.byte_size or downloaded.sha256 != artifact.sha256:
            raise BuildNotFoundError(build_id)
        return artifact.content_type

    return await media_file(fetch)


@get(
    "/",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="builds_list",
    security=[ANONYMOUS, SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only()),
)
async def list_builds(
    build_queries: BuildQueries,
    permissions: Permissions,
    caller: CurrentCaller,
    status: FromQuery[BuildStatusFilter | None] = None,
    category: FromQuery[BuildCategory | None] = None,
    version_id: Annotated[int | None, QueryParameter(ge=1)] = None,
    tag_id: FromQuery[list[int] | None] = None,
    tag_slug: FromQuery[list[str] | None] = None,
    account_id: FromQuery[UUID | None] = None,
    submitted_by: FromQuery[Literal["me"] | None] = None,
    sort: FromQuery[BuildListSort] = BuildListSort.ID,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Response[Page[PublicBuildSummary]]:
    """List builds in a named order, approved ones unless another status is selected.

    Every tag_id and tag_slug must match. account_id selects builds crediting that account as a
    creator or designer. With submitted_by=me the caller sees their own builds in every status;
    other unpublished listings require build.submission.view_pending permission. Text search
    lives at /v1/search.
    """
    submitter_id = caller.require_consented_account() if submitted_by is not None else None
    if status is not None:
        statuses = frozenset({status.to_domain()})
    else:
        statuses = frozenset(BuildStatus) if submitter_id is not None else frozenset({BuildStatus.APPROVED})
    if statuses != {BuildStatus.APPROVED} and submitter_id is None:
        await _require_pending_view(permissions, caller)
    page = await build_queries.list_page(
        statuses=statuses,
        submitter_account_id=submitter_id,
        filters=BuildListFilters(
            category,
            version_id,
            tag_ids=tuple(dict.fromkeys(tag_id or ())),
            tag_slugs=tuple(dict.fromkeys(tag_slug or ())),
            creator_id=account_id,
        ),
        sort=sort,
        selector=resolve_selector(offset=offset, after_id=after_id, before_id=before_id),
        page_size=page_size,
        context=caller.context(),
    )
    personal = submitter_id is not None or statuses != {BuildStatus.APPROVED}
    return Response(
        Page[PublicBuildSummary].from_domain(page, PublicBuildSummary.from_build),
        headers={"Cache-Control": "no-store"} if personal else None,
    )


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
    },
    path="/builds",
    tags=["builds"],
    route_handlers=[edit_build, get_build, get_build_form, get_build_history, get_build_media, list_builds],
)
