"""Build read and write routes."""

import re
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Query, Response

from squid.accounts.errors import ConsentRequiredError
from squid.api.dependencies import BuildCommands, BuildQueries, CurrentCaller, Permissions, Search
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    anchor,
    parse_page_sort,
    render_page,
    resolve_selector,
)
from squid.api.security import Caller, caller_allows, requires, subject_for
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildStatusFilter, BuildSummary, DoorSubmission
from squid.api.v1.search import PUBLIC_SEARCH_STATUSES, build_hit_id, hydrate_builds
from squid.builds.application import (
    BUILD_SORT_FIELDS,
    BuildEditor,
    BuildEditPatch,
    BuildListSort,
    BuildSortField,
    DoorSubmissionInput,
)
from squid.builds.domain import Build
from squid.builds.errors import (
    BuildRevisionMismatchError,
    BuildRevisionRequiredError,
    InvalidBuildError,
)
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.permissions.application import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_CREATE, BUILD_SUBMISSION_VIEW_PENDING
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort

router = APIRouter(prefix="/builds", tags=["builds"])
UserWriter = Annotated[Caller, Depends(requires(BUILD_SUBMISSION_CREATE))]
_BUILD_ETAG = re.compile(r'^"build-(?P<build_id>[1-9][0-9]*)-r(?P<revision>[1-9][0-9]*)"$')


@router.post(
    "",
    response_model=BuildDetail,
    status_code=201,
    responses=responses(400, 401, 403, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def submit_build(
    submission: DoorSubmission,
    response: Response,
    builds: BuildCommands,
    caller: UserWriter,
) -> BuildDetail:
    """Submit a door build for Discord moderation."""
    _require_consented_user(caller)
    if submission.category.casefold() != "door":
        msg = "Only door submissions are supported."
        raise InvalidBuildError(msg, public_context={"category": submission.category})
    assert caller.account_id is not None  # `_require_consented_user` rejects a caller without one
    build = await builds.submit_door(
        DoorSubmissionInput(
            submitter_account_id=caller.account_id,
            door_size=submission.door_size,
            pattern=tuple(submission.pattern),
            door_type=submission.door_type,
            build_size=submission.build_size,
            works_in=submission.works_in,
            restrictions=tuple(submission.restrictions),
            information_about_build=submission.information_about_build,
            normal_closing_time=submission.normal_closing_time,
            normal_opening_time=submission.normal_opening_time,
            date_of_creation=submission.date_of_creation,
            creators=tuple(submission.creators),
            locationality=submission.locationality,
            directionality=submission.directionality,
            image_urls=tuple(submission.image_urls),
            video_urls=tuple(submission.video_urls),
            world_download_urls=tuple(submission.world_download_urls),
            schematic_urls=tuple(submission.schematic_urls),
            ai_generated=False,
        )
    )
    _set_build_etag(response, build)
    return BuildDetail.from_domain(build)


@router.patch(
    "/{build_id}",
    response_model=BuildDetail,
    responses=responses(400, 401, 403, 404, 409, 412, 422, 428, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def edit_build(
    build_id: int,
    changes: BuildPatch,
    response: Response,
    builds: BuildCommands,
    caller: UserWriter,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> BuildDetail:
    """Edit an owned pending build, or any build with `build.submission.edit`."""
    _require_consented_user(caller)
    build = await builds.apply_edit(
        BuildEditor(subject=subject_for(caller)),
        build_id,
        BuildEditPatch.from_attributes(changes.edit_attributes()),
        expected_revision=_expected_revision(build_id, if_match),
    )
    _set_build_etag(response, build)
    return BuildDetail.from_domain(build)


@router.get(
    "/{build_id}",
    response_model=BuildDetail,
    responses=responses(
        404,
        422,
        503,
        describe={404: "No confirmed build with this identifier. A pending build answers 404 as well."},
    ),
)
async def get_build(build_id: int, response: Response, build_queries: BuildQueries) -> BuildDetail:
    """Return one confirmed public build.

    A pending build answers 404, not 403: a submission's existence is private
    until it is confirmed, so "not published" and "not there" have to be
    indistinguishable to a caller without `build.submission.view_pending`.
    """
    build = await build_queries.get_public(build_id)
    _set_build_etag(response, build)
    return BuildDetail.from_domain(build)


@router.get("", response_model=Page[BuildSummary], responses=responses(400, 401, 403, 422, 503))
async def list_builds(
    build_queries: BuildQueries,
    search_service: Search,
    permissions: Permissions,
    caller: CurrentCaller,
    q: Annotated[str | None, Query(max_length=1_000)] = None,
    status: BuildStatusFilter | None = None,
    sort: Annotated[str | None, Query(max_length=80)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[BuildSummary]:
    """Search public builds, or list one authoritative moderation-status view."""
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
            next=anchor(result.next),
            prev=anchor(result.prev),
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
    return render_page(page, BuildSummary.from_domain)


async def _require_pending_view(permissions: PermissionService, caller: Caller) -> None:
    """Gate non-public moderation views on the node, credential included.

    "A service key never reads unreviewed submissions" used to be a hardcoded
    branch on the caller kind. It is now an expressible policy: no key is
    issued `build.submission.view_pending` by default, so a leaked key still
    cannot read them -- and a key that should read them can be given one,
    which the branch made impossible.
    """
    if not await caller_allows(permissions, caller, BUILD_SUBMISSION_VIEW_PENDING):
        raise AuthenticationError if caller.kind == "anonymous" else AuthorizationError


def _require_consented_user(caller: Caller) -> None:
    if caller.kind != "account" or caller.discord_id is None or caller.account_id is None:
        raise AuthenticationError
    if caller.consent_pending:
        raise ConsentRequiredError(caller.discord_id, account_id=caller.account_id).with_context(
            public_context={"consent_url": "/v1/users/me/consent"},
            end_user_action="Accept the current privacy notice and retry.",
        )


def build_etag(build: Build) -> str:
    """Return the strong validator for one persisted build revision."""
    if build.id is None:
        msg = "Cannot create an ETag for an unpersisted build."
        raise ValueError(msg)
    return f'"build-{build.id}-r{build.revision}"'


def _set_build_etag(response: Response, build: Build) -> None:
    response.headers["ETag"] = build_etag(build)


def _expected_revision(build_id: int, if_match: str | None) -> int:
    if if_match is None:
        raise BuildRevisionRequiredError(build_id)
    match = _BUILD_ETAG.fullmatch(if_match.strip())
    if match is None or int(match.group("build_id")) != build_id:
        raise BuildRevisionMismatchError(build_id, expected_revision=None)
    return int(match.group("revision"))
