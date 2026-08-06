"""HTTP credential principals and declarative scope checks."""

import hmac
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from squid.core.errors import AuthenticationError, AuthorizationError


class Scope(StrEnum):
    """Capabilities assignable to API credentials."""

    BUILDS_READ = "builds:read"
    BUILDS_WRITE = "builds:write"
    VERIFY = "verify"
    VOTES_CAST = "votes:cast"
    USERS_READ = "users:read"


@dataclass(frozen=True, slots=True)
class Principal:
    """Transport-neutral authenticated or anonymous caller identity."""

    kind: Literal["anonymous", "service", "user"]
    subject: str
    scopes: frozenset[Scope] = frozenset()
    discord_id: int | None = None
    user_id: int | None = None


ANONYMOUS = Principal(kind="anonymous", subject="anonymous", scopes=frozenset({Scope.BUILDS_READ}))
_authorization = APIKeyHeader(name="Authorization", scheme_name="ApiCredential", auto_error=False)


def require(scope: Scope):
    """Build a FastAPI dependency requiring one credential scope."""

    async def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if scope not in principal.scopes:
            if principal.kind == "anonymous":
                raise AuthenticationError
            raise AuthorizationError
        return principal

    return check


async def current_principal(
    request: Request,
    authorization: Annotated[str | None, Security(_authorization)],
) -> Principal:
    """Authenticate a legacy bootstrap secret or an indexed API key."""
    if authorization is None:
        return ANONYMOUS
    config = request.app.state.config
    if hmac.compare_digest(authorization, config.api.secret.get_secret_value()):
        return Principal(
            kind="service",
            subject="legacy-bootstrap",
            scopes=frozenset(Scope),
        )
    token = authorization.removeprefix("Bearer ")
    api_keys = request.app.state.runtime.services.api_keys
    if api_keys is None:
        raise AuthenticationError
    used_ip = request.client.host if request.client is not None else None
    key = await api_keys.authenticate(token, used_ip=used_ip)
    if key is None:
        raise AuthenticationError
    valid_scopes = frozenset(Scope(value) for value in key.scopes if value in Scope._value2member_map_)
    return Principal(
        kind="service",
        subject=f"api-key:{key.key_id}",
        scopes=valid_scopes,
        user_id=key.owner_user_id,
    )
