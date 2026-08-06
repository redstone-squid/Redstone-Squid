"""Build read routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from squid.api.dependencies import CursorSigner, Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.security import Principal, Scope, require
from squid.api.v1.schemas.builds import BuildDetail, BuildPatch, BuildSummary, DoorSubmission
from squid.builds.application import BuildEditPatch, DoorSubmissionInput
from squid.builds.domain import Status
from squid.builds.errors import BuildNotFoundError, InvalidBuildError
from squid.core.errors import AuthenticationError, AuthorizationError, ErrorCode, ValidationError
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection
from squid.users.errors import ConsentRequiredError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/builds", tags=["builds"])
_PUBLIC_STATUSES = frozenset({Status.CONFIRMED})
_PUBLIC_SEARCH_STATUSES = frozenset({"confirmed"})
UserWriter = Annotated[Principal, Depends(require(Scope.BUILDS_WRITE))]


@router.post("", response_model=BuildDetail, status_code=201, responses=responses(400, 401, 403, 422, 503))
async def submit_build(submission: DoorSubmission, services: Services, principal: UserWriter) -> BuildDetail:
    """Submit a door build for Discord moderation."""
    _require_consented_user(principal)
    if submission.category.casefold() != "door":
        msg = "Only door submissions are supported."
        raise InvalidBuildError(msg, public_context={"category": submission.category})
    assert principal.discord_id is not None
    build = await services.builds.submit_door(
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
    return BuildDetail.from_domain(build)


@router.patch("/{build_id}", response_model=BuildDetail, responses=responses(400, 401, 403, 404, 409, 422, 503))
async def edit_build(
    build_id: int,
    changes: BuildPatch,
    services: Services,
    principal: UserWriter,
) -> BuildDetail:
    """Edit an owned pending build, or any build as a global administrator."""
    _require_consented_user(principal)
    patch = BuildEditPatch.from_attributes(changes.model_dump(exclude_unset=True))
    assert principal.discord_id is not None
    async with services.builds.edit(build_id, patch, blocking=False) as lease:
        is_owner = lease.build.submission_status is Status.PENDING and lease.build.submitter_id == principal.discord_id
        is_admin = await services.authorization.is_global_administrator(principal.discord_id)
        if not is_owner and not is_admin:
            raise AuthorizationError
        build = await lease.commit()
    return BuildDetail.from_domain(build)


@router.get("/{build_id}", response_model=BuildDetail, responses=responses(404, 422, 503))
async def get_build(build_id: int, services: Services) -> BuildDetail:
    """Return one confirmed public build."""
    build = await services.build_queries.get(build_id)
    if build is None or build.submission_status is not Status.CONFIRMED:
        raise BuildNotFoundError(build_id)
    return BuildDetail.from_domain(build)


@router.get("", response_model=Page[BuildSummary], responses=responses(400, 422, 503))
async def list_builds(
    services: Services,
    signer: CursorSigner,
    q: Annotated[str | None, Query(max_length=1_000)] = None,
    sort: Annotated[str | None, Query(max_length=80)] = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[BuildSummary]:
    """Search public builds, or list the authoritative confirmed-build view."""
    if q is not None:
        request = SearchRequest(
            query=q,
            scope=SearchScope.BUILDS,
            mode=SearchMode.LEXICAL,
            page_size=page_size,
            cursor=cursor,
            sort=_parse_sort(sort),
            visible_statuses=_PUBLIC_SEARCH_STATUSES,
        )
        result = await services.search.search(request)
        hit_ids = [_build_id(hit.source_id) for hit in result.hits if hit.resource_kind == "build"]
        builds = await services.build_queries.get_many(hit_ids)
        found_ids = {build.id for build in builds}
        missing = [build_id for build_id in hit_ids if build_id not in found_ids]
        if missing:
            logger.warning("Search projection referenced missing builds", extra={"build_ids": missing})
        return Page(
            items=[BuildSummary.from_domain(build) for build in builds],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    if sort is not None:
        msg = "sort is only supported with q"
        raise ValidationError(
            msg,
            public_context={"field": "sort"},
        )
    binding = "builds:status=confirmed:id-desc"
    after_id = _after_id(signer, cursor, binding)
    builds = await services.build_queries.list_page(
        statuses=_PUBLIC_STATUSES,
        submitter_id=None,
        after_id=after_id,
        limit=page_size + 1,
    )
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


def _parse_sort(value: str | None) -> SearchSort | None:
    if value is None:
        return None
    direction = SortDirection.DESCENDING if value.startswith("-") else SortDirection.ASCENDING
    field = value.removeprefix("-")
    if not field:
        msg = "sort field is required"
        raise ValidationError(msg, code=ErrorCode.INVALID_QUERY)
    return SearchSort(field, direction)


def _build_id(source_id: str) -> int:
    try:
        return int(source_id)
    except ValueError as error:
        msg = "search returned an invalid build identifier"
        raise ValidationError(msg) from error


def _after_id(signer: CursorSigner, cursor: str | None, binding: str) -> int | None:
    if cursor is None:
        return None
    value = signer.decode(cursor, binding=binding).get("after_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = "cursor payload contains an invalid build identifier"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value


def _require_consented_user(principal: Principal) -> None:
    if principal.kind != "user" or principal.discord_id is None:
        raise AuthenticationError
    if principal.consent_pending:
        raise ConsentRequiredError(principal.discord_id).with_context(
            public_context={"consent_url": "/v1/users/me/consent"},
            end_user_action="Accept the current privacy notice and retry.",
        )
