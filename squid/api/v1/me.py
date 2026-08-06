"""Authenticated self-account routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from squid.api.dependencies import CursorSigner, Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.security import Principal, Scope, require
from squid.api.v1.builds import after_id_from_cursor, keyset_page
from squid.api.v1.schemas.builds import BuildStatusFilter, BuildSummary
from squid.api.v1.schemas.me import UserMe
from squid.builds.domain import Status
from squid.core.errors import AuthenticationError
from squid.users.errors import UserNotFoundError

router = APIRouter(prefix="/users/me", tags=["users"])
UserPrincipal = Annotated[Principal, Depends(require(Scope.USERS_READ))]
_ALL_STATUSES = frozenset(Status)


@router.get("", response_model=UserMe, responses=responses(401, 403, 404, 503))
async def get_me(services: Services, principal: UserPrincipal) -> UserMe:
    """Return the authenticated user's own linked account."""
    if principal.kind != "user" or principal.discord_id is None:
        raise AuthenticationError
    account = await services.users.get_account(principal.discord_id)
    if account is None:
        raise UserNotFoundError(principal.discord_id)
    return UserMe.from_domain(account, consent_pending=principal.consent_pending)


@router.post("/consent", response_model=UserMe, responses=responses(401, 403, 404, 503))
async def grant_consent(services: Services, principal: UserPrincipal) -> UserMe:
    """Accept the current privacy notice for future writes."""
    if principal.kind != "user" or principal.discord_id is None:
        raise AuthenticationError
    account = await services.users.grant_current_consent(principal.discord_id)
    return UserMe.from_domain(account, consent_pending=False)


@router.get("/builds", response_model=Page[BuildSummary], responses=responses(400, 401, 403, 422, 503))
async def list_my_builds(
    services: Services,
    signer: CursorSigner,
    principal: UserPrincipal,
    status: BuildStatusFilter | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[BuildSummary]:
    """List the caller's own submissions, including the ones still awaiting review.

    This is the authoritative counterpart to `GET /v1/builds`: submitters need to see their own
    pending and denied builds, which the public search path deliberately cannot return.
    """
    if principal.kind != "user" or principal.discord_id is None:
        raise AuthenticationError
    statuses = _ALL_STATUSES if status is None else frozenset({status.to_domain()})
    binding = f"users:{principal.discord_id}:builds:status={status or 'all'}:id-desc"
    builds = await services.build_queries.list_page(
        statuses=statuses,
        submitter_id=principal.discord_id,
        after_id=after_id_from_cursor(signer, cursor, binding),
        limit=page_size + 1,
    )
    return keyset_page(signer, builds, page_size=page_size, binding=binding)
