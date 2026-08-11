"""HTTP credential principals and declarative scope checks."""

import hmac
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from squid.cli_auth.application import CLI_SESSION_TOKEN_PREFIX
from squid.cli_auth.errors import CliAuthorizationError
from squid.core.errors import AuthenticationError, AuthorizationError
from squid.minecraft_auth.application.crypto import INSTALLATION_TOKEN_PREFIX, PLAYER_TOKEN_PREFIX
from squid.minecraft_auth.errors import MinecraftAuthorizationError


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

    kind: Literal["anonymous", "service", "account", "cli", "minecraft_player"]
    subject: str
    scopes: frozenset[Scope] = frozenset()
    discord_id: int | None = None
    account_id: int | None = None
    consent_pending: bool = False
    minecraft_origin: Literal["paper", "fabric"] | None = None
    java_uuid: UUID | None = None
    installation_id: UUID | None = None
    grant_id: UUID | None = None
    cli_device_id: UUID | None = None
    cli_session_id: UUID | None = None


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
        session_token = request.cookies.get("__Host-squid_session")
        web_auth = request.app.state.runtime.services.web_auth
        if session_token is None or web_auth is None:
            return ANONYMOUS
        identity = await web_auth.authenticate(session_token)
        if identity is None:
            raise AuthenticationError
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf_cookie = request.cookies.get("squid_csrf")
            csrf_header = request.headers.get("X-CSRF-Token")
            if csrf_cookie is None or csrf_header is None or not hmac.compare_digest(csrf_cookie, csrf_header):
                raise AuthorizationError
        return Principal(
            kind="account",
            subject=f"account:{identity.account_id}",
            scopes=frozenset(Scope),
            discord_id=identity.discord_id,
            account_id=identity.account_id,
            consent_pending=identity.consent_pending,
        )
    if len(authorization) > 4096:
        raise AuthenticationError
    config = request.app.state.config
    if hmac.compare_digest(authorization, config.api.secret.get_secret_value()):
        return Principal(
            kind="service",
            subject="legacy-bootstrap",
            scopes=frozenset(Scope),
        )
    token = authorization.removeprefix("Bearer ")
    if token.startswith(f"{CLI_SESSION_TOKEN_PREFIX}_"):
        cli_authorization = request.app.state.runtime.services.cli_authorization
        if cli_authorization is None:
            raise AuthenticationError
        try:
            identity = await cli_authorization.authenticate(token)
        except CliAuthorizationError:
            raise AuthenticationError from None
        return Principal(
            kind="cli",
            subject=f"cli-session:{identity.session_id}",
            scopes=frozenset(Scope),
            account_id=identity.account_id,
            consent_pending=identity.consent_pending,
            cli_device_id=identity.device_id,
            cli_session_id=identity.session_id,
        )
    if token.startswith(f"{PLAYER_TOKEN_PREFIX}_"):
        players = request.app.state.runtime.services.minecraft_player_authorization
        installations = request.app.state.runtime.services.minecraft_installations
        if players is None:
            raise AuthenticationError
        try:
            installation_id = request.headers.get("X-Squid-Installation-ID")
            installation_secret = request.headers.get("X-Squid-Installation-Secret")
            if installation_id is None and installation_secret is None:
                context = await players.authenticate_fabric_player(token)
            else:
                if installations is None or installation_id is None or installation_secret is None:
                    raise AuthenticationError
                if len(installation_id) > 36 or not 32 <= len(installation_secret) <= 512:
                    raise AuthenticationError
                parsed_id = UUID(installation_id)
                installation = await installations.authenticate(
                    f"{INSTALLATION_TOKEN_PREFIX}_{parsed_id.hex}_{installation_secret}"
                )
                context = await players.authenticate_paper_player(token, installation)
        except (MinecraftAuthorizationError, ValueError):
            raise AuthenticationError from None
        return Principal(
            kind="minecraft_player",
            subject=f"minecraft-grant:{context.grant_id}",
            account_id=context.account_id,
            minecraft_origin=context.origin.value,
            java_uuid=context.java_uuid,
            installation_id=context.installation_id,
            grant_id=context.grant_id,
        )
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
        account_id=key.owner_account_id,
    )
