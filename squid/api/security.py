"""HTTP credential callers and declarative permission-node checks."""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request, Response, Security
from fastapi.security import APIKeyHeader

from squid.accounts.errors import ConsentRequiredError
from squid.cli_auth.application import CLI_SESSION_TOKEN_PREFIX
from squid.cli_auth.errors import CliAuthorizationError
from squid.core.actors import Actor
from squid.core.errors import AuthenticationError, AuthorizationError, DomainError
from squid.minecraft_auth.application.crypto import PLAYER_TOKEN_PREFIX
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import CATALOGUE, Pattern, PermissionNode


@dataclass(frozen=True, slots=True)
class Caller:
    """Identity and credential permissions used to authorize an HTTP request.

    Browser sessions, CLI sessions, Minecraft grants, and API keys share this
    representation. `nodes` limits the credential; the permission service checks
    the account's authority separately. Both must allow an operation when the
    credential has an owner. Ownerless service credentials use their nodes alone.
    """

    kind: Literal["anonymous", "service", "account", "cli", "minecraft_player"]
    subject: str
    nodes: frozenset[Pattern] = field(default_factory=frozenset)
    """Parsed patterns, so authorization matches rather than re-parses per check."""
    account_id: int | None = None
    """Account that owns the credential, or None for anonymous and ownerless service callers.

    Resolve linked provider identities through the account service when needed.
    HTTP permission checks use the account ID and do not use a Discord guild.
    """
    consent_pending: bool = False
    minecraft_origin: Literal["paper", "fabric"] | None = None
    java_uuid: UUID | None = None
    installation_id: UUID | None = None
    grant_id: UUID | None = None
    cli_device_id: UUID | None = None
    cli_session_id: UUID | None = None

    async def allows(
        self,
        permissions: PermissionService,
        node: PermissionNode,
    ) -> bool:
        """Return whether the credential and its account permit the operation.

        Ownerless service credentials need only a matching credential pattern.
        Every other caller, including anonymous callers, also passes through the
        permission service. Changing an account's permissions therefore affects its
        existing keys without editing each key.
        """
        if not self.credential_allows(node):
            return False
        if self.kind == "service" and self.account_id is None:
            return True
        return await permissions.allows(self.to_actor(), node)

    def credential_allows(self, node: PermissionNode) -> bool:
        """Whether the credential itself carries `node`, before its owner is consulted."""
        return any(pattern.matches(node) for pattern in self.nodes)

    def to_actor(self) -> Actor:
        """Return the account subject used for HTTP permission checks.

        No guild is supplied: a permission granted in one Discord server must not
        authorize an HTTP request outside that server.
        """
        return Actor(account_id=self.account_id, guild_id=None)

    def require_consented_account(self) -> int:
        """Return the caller's account ID after checking privacy consent.

        Accept any credential with an account ID, including CLI and Minecraft
        credentials. Raise AuthenticationError without an account, or
        ConsentRequiredError with consent-page links when acceptance is pending.
        """
        if self.account_id is None:
            raise AuthenticationError
        if self.consent_pending:
            raise ConsentRequiredError(account_id=self.account_id).with_context(
                public_context={"consent_url": "/v1/users/me/consent", "notice_url": "/v1/consent/notice"},
                end_user_action="Accept the current privacy notice and retry.",
            )
        return self.account_id


UNBOUNDED = frozenset({Pattern.parse("**")})
"""A credential that is not itself narrowed; its holder's own authority decides."""

ANONYMOUS = Caller(kind="anonymous", subject="anonymous", nodes=UNBOUNDED)
"""No credential restriction; the permission service still checks the anonymous subject."""
_authorization = APIKeyHeader(name="Authorization", scheme_name="ApiCredential", auto_error=False)


def requires(node: PermissionNode | str) -> Callable[[Request, Caller], Awaitable[Caller]]:
    """Build a FastAPI dependency requiring one permission node.

    Check both credential restrictions and account permissions through
    `Caller.allows`. Denial raises AuthenticationError for an anonymous caller
    and AuthorizationError for an authenticated caller.
    """
    required = CATALOGUE[node] if isinstance(node, str) else node

    async def check(request: Request, caller: Annotated[Caller, Depends(current_caller)]) -> Caller:
        permissions = request.app.state.runtime.services.permissions
        if not await caller.allows(permissions, required):
            raise AuthenticationError if caller.kind == "anonymous" else AuthorizationError
        return caller

    return check


async def current_caller(
    request: Request,
    authorization: Annotated[str | None, Security(_authorization)],
) -> Caller:
    """Resolve a browser session, API key, CLI session, or Minecraft player grant.

    An Authorization header takes precedence over the browser cookie. With
    neither, return the anonymous caller. Browser writes also require matching
    CSRF cookie and header values. Invalid supplied credentials raise
    AuthenticationError rather than falling back to another credential.
    """
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
            csrf_header = request.headers.get("CSRF-Token")
            if csrf_cookie is None or csrf_header is None or not hmac.compare_digest(csrf_cookie, csrf_header):
                raise AuthorizationError
        return Caller(
            kind="account",
            subject=f"account:{identity.account_id}",
            nodes=UNBOUNDED,
            account_id=identity.account_id,
            consent_pending=identity.consent_pending,
        )
    if len(authorization) > 4096:
        raise AuthenticationError
    config = request.app.state.config
    if hmac.compare_digest(authorization, config.api.secret.get_secret_value()):
        # The bootstrap credential has no account owner. Its configured patterns
        # are its full authority; account-only operations reject it separately.
        return Caller(
            kind="service",
            subject="legacy-bootstrap",
            nodes=config.api.secret_patterns,
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
        return Caller(
            kind="cli",
            subject=f"cli-session:{identity.session_id}",
            nodes=UNBOUNDED,
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
            installation_id = request.headers.get("Squid-Installation-ID")
            installation_secret = request.headers.get("Squid-Installation-Secret")
            if installation_id is None and installation_secret is None:
                context = await players.authenticate_fabric_player(token)
            else:
                if installations is None or installation_id is None or installation_secret is None:
                    raise AuthenticationError
                installation = await installations.authenticate_headers(installation_id, installation_secret)
                context = await players.authenticate_paper_player(token, installation)
        except DomainError, ValueError:
            raise AuthenticationError from None
        return Caller(
            kind="minecraft_player",
            subject=f"minecraft-grant:{context.grant_id}",
            nodes=UNBOUNDED,
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
    return Caller(
        kind="service",
        subject=f"api-key:{key.key_id}",
        nodes=key.scopes,
        account_id=key.owner_account_id,
    )


async def current_browser_account_id(caller: Annotated[Caller, Depends(current_caller)]) -> int:
    """Require a signed-in browser account with current privacy consent."""
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    return caller.require_consented_account()


BrowserAccountId = Annotated[int, Depends(current_browser_account_id)]


async def prevent_credential_storage(response: Response) -> None:
    """Keep credential-bearing responses out of browser and intermediary caches."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def consented_account_id(caller: Annotated[Caller, Depends(current_caller)]) -> int:
    """Dependency form, for routes whose only precondition is a consented account."""
    return caller.require_consented_account()


ConsentedAccountId = Annotated[int, Depends(consented_account_id)]
"""A signed-in account that has accepted the current notice."""
