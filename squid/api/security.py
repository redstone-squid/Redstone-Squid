"""HTTP credential principals and declarative scope checks."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import Depends

from squid.core.errors import AuthorizationError


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


def require(scope: Scope):
    """Build a FastAPI dependency requiring one credential scope."""

    async def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if scope not in principal.scopes:
            raise AuthorizationError
        return principal

    return check


async def current_principal() -> Principal:
    """Return the anonymous principal until credential transports are configured."""
    return ANONYMOUS
