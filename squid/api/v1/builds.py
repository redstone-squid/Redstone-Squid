"""Build read and write routes."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from squid.accounts.errors import ConsentRequiredError
from squid.api.dependencies import BuildCommands, BuildQueries, CurrentPrincipal, CursorSigner, Permissions, Search
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import Page
from squid.api.security import Principal, principal_allows, requires, subject_for
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildStatusFilter, BuildSummary, DoorSubmission
from squid.api.v1.search import PUBLIC_SEARCH_STATUSES, build_hit_id, hydrate_builds, parse_sort
from squid.builds.application import BuildEditPatch, DoorSubmissionInput
from squid.builds.domain import Build, Status
from squid.builds.errors import (
    BuildNotFoundError,
    BuildRevisionMismatchError,
    BuildRevisionRequiredError,
    InvalidBuildError,
)
from squid.core.errors import AuthenticationError, AuthorizationError, ErrorCode, ValidationError
from squid.permissions.application import PermissionService
from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_CREATE,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.search.domain import SearchMode, SearchRequest, SearchScope

router = APIRouter(prefix="/builds", tags=["builds"])
UserWriter = Annotated[Principal, Depends(requires(BUILD_SUBMISSION_CREATE))]
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
    principal: UserWriter,
) -> BuildDetail:
    """Submit a door build for Discord moderation."""
    _require_consented_user(principal)
    if submission.category.casefold() != "door":
        msg = "Only door submissions are supported."
        raise InvalidBuildError(msg, public_context={"category": submission.category})
    assert principal.discord_id is not None
    build = await builds.submit_door(
        DoorSubmissionInput(
            submitter_id=principal.discord_id,
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
    permissions: Permissions,
    principal: UserWriter,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> BuildDetail:
    """Edit an owned pending build, or any build as a global administrator."""
    _require_consented_user(principal)
    expected_revision = _expected_revision(build_id, if_match)
    patch = BuildEditPatch.from_attributes(changes.edit_attributes())
    assert principal.discord_id is not None
    async with builds.edit(
        build_id,
        patch,
        blocking=False,
        expected_revision=expected_revision,
    ) as lease:
        is_owner = lease.build.submission_status is Status.PENDING and lease.build.submitter_id == principal.discord_id
        if not is_owner and not await permissions.allows(subject_for(principal), BUILD_SUBMISSION_EDIT):
            raise AuthorizationError
        build = await lease.commit()
    _set_build_etag(response, build)
    return BuildDetail.from_domain(build)


@router.get("/{build_id}", response_model=BuildDetail, responses=responses(404, 422, 503))
async def get_build(build_id: int, response: Response, build_queries: BuildQueries) -> BuildDetail:
    """Return one confirmed public build."""
    build = await build_queries.get(build_id)
    if build is None or build.submission_status is not Status.CONFIRMED:
        raise BuildNotFoundError(build_id)
    _set_build_etag(response, build)
    return BuildDetail.from_domain(build)


@router.get("", response_model=Page[BuildSummary], responses=responses(400, 401, 403, 422, 503))
async def list_builds(
    build_queries: BuildQueries,
    search_service: Search,
    permissions: Permissions,
    signer: CursorSigner,
    principal: CurrentPrincipal,
    q: Annotated[str | None, Query(max_length=1_000)] = None,
    status: BuildStatusFilter | None = None,
    sort: Annotated[str | None, Query(max_length=80)] = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[BuildSummary]:
    """Search public builds, or list one authoritative moderation-status view."""
    if q is not None:
        if status is not None:
            msg = "status cannot be combined with q"
            raise ValidationError(msg, public_context={"field": "status"})
        result = await search_service.search(
            SearchRequest(
                query=q,
                scope=SearchScope.BUILDS,
                mode=SearchMode.LEXICAL,
                page_size=page_size,
                cursor=cursor,
                sort=parse_sort(sort),
                visible_statuses=PUBLIC_SEARCH_STATUSES,
            )
        )
        summaries = await hydrate_builds(build_queries, result.hits)
        return Page(
            items=[
                summary for hit in result.hits if (summary := summaries.get(build_hit_id(hit.source_id))) is not None
            ],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    if sort is not None:
        msg = "sort is only supported with q"
        raise ValidationError(
            msg,
            public_context={"field": "sort"},
        )
    effective = status or BuildStatusFilter.CONFIRMED
    if effective is not BuildStatusFilter.CONFIRMED:
        await _require_pending_view(permissions, principal)
    binding = f"builds:status={effective}:id-desc"
    after_id = after_id_from_cursor(signer, cursor, binding)
    builds = await build_queries.list_page(
        statuses=frozenset({effective.to_domain()}),
        submitter_id=None,
        after_id=after_id,
        limit=page_size + 1,
    )
    return keyset_page(signer, builds, page_size=page_size, binding=binding)


async def _require_pending_view(permissions: PermissionService, principal: Principal) -> None:
    """Gate non-public moderation views on the node, credential included.

    "A service key never reads unreviewed submissions" used to be a hardcoded
    branch on the principal kind. It is now an expressible policy: no key is
    issued `build.submission.view_pending` by default, so a leaked key still
    cannot read them -- and a key that should read them can be given one,
    which the branch made impossible.
    """
    if not await principal_allows(permissions, principal, BUILD_SUBMISSION_VIEW_PENDING):
        raise AuthenticationError if principal.kind == "anonymous" else AuthorizationError


def keyset_page(
    signer: CursorSigner,
    builds: list[Build],
    *,
    page_size: int,
    binding: str,
) -> Page[BuildSummary]:
    """Render one descending-ID page from a `limit + 1` overfetch."""
    has_more = len(builds) > page_size
    page_builds = builds[:page_size]
    next_cursor = None
    if has_more and page_builds:
        assert page_builds[-1].id is not None
        next_cursor = signer.encode({"after_id": page_builds[-1].id}, binding=binding)
    return Page(
        items=[BuildSummary.from_domain(build) for build in page_builds],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def after_id_from_cursor(signer: CursorSigner, cursor: str | None, binding: str) -> int | None:
    if cursor is None:
        return None
    value = signer.decode(cursor, binding=binding).get("after_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = "cursor payload contains an invalid build identifier"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value


def _require_consented_user(principal: Principal) -> None:
    if principal.kind != "account" or principal.discord_id is None or principal.account_id is None:
        raise AuthenticationError
    if principal.consent_pending:
        raise ConsentRequiredError(principal.discord_id).with_context(
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
