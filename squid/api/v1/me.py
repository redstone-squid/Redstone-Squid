"""Authenticated self-account routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.security import Principal, Scope, require
from squid.api.v1.schemas.me import UserMe
from squid.core.errors import AuthenticationError
from squid.users.errors import UserNotFoundError

router = APIRouter(prefix="/users/me", tags=["users"])
UserPrincipal = Annotated[Principal, Depends(require(Scope.USERS_READ))]


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
