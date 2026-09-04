"""HTTP credential callers and declarative permission-node checks."""

import hmac
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from squid.accounts.errors import ConsentRequiredError
from squid.cli_auth.application import CLI_SESSION_TOKEN_PREFIX
from squid.cli_auth.errors import CliAuthorizationError
from squid.core.errors import AuthenticationError, AuthorizationError, DomainError
from squid.minecraft_auth.application.crypto import PLAYER_TOKEN_PREFIX
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import CATALOGUE, Pattern, PermissionNode, Subject


@dataclass(frozen=True, slots=True)
class Caller:
    """One authenticated or anonymous caller, whatever credential it arrived with.

    HTTP API keys, the legacy bootstrap secret, browser sessions, CLI sessions,
    and Minecraft player grants all reduce to this one type, so a route
    authorizes once instead of branching on how the caller signed in.

    `nodes` bounds what a *credential* may do; the permission engine decides what
    its owner may do. A service key needs both, which is AWS's permissions-
    boundary rule: revoking the owner's node instantly defangs every key they
    issued, and a key can never exceed the human behind it.
    """

    kind: Literal["anonymous", "service", "account", "cli", "minecraft_player"]
    subject: str
    nodes: frozenset[Pattern] = field(default_factory=frozenset)
    """Parsed patterns, so authorization matches rather than re-parses per check."""
    account_id: int | None = None
    """There is deliberately no `discord_id` beside this.

    `subject_for` hardcodes `guild_id=None`, so an HTTP caller can never act on a
    Discord fact anyway -- a snowflake here would be an identifier with no legitimate
    HTTP use. Keeping one "as an optional convenience" is exactly the affordance that
    produced a `assert ... is not None` on a submission path already holding a perfectly
    good account id. `UserMe.discord_id` in the *response* stays: it is read off the
    account's identities, which is the correct pattern.
    """
    consent_pending: bool = False
    minecraft_origin: Literal["paper", "fabric"] | None = None
    java_uuid: UUID | None = None
    installation_id: UUID | None = None
    grant_id: UUID | None = None
    cli_device_id: UUID | None = None
    cli_session_id: UUID | None = None


UNBOUNDED = frozenset({Pattern.parse("**")})
"""A credential that is not itself narrowed; its holder's own authority decides."""

ANONYMOUS = Caller(kind="anonymous", subject="anonymous", nodes=UNBOUNDED)
"""Unbounded as a *credential*, which costs nothing: the subject behind it holds
no account and therefore reaches only default-allow nodes."""
_authorization = APIKeyHeader(name="Authorization", scheme_name="ApiCredential", auto_error=False)


def requires(node: PermissionNode | str):
    """Build a FastAPI dependency requiring one permission node.

    Two questions, both of which must answer yes for a service key: does the
    credential carry the node, and does the account behind it hold it? An
    anonymous caller carries nothing, so it reaches only default-allow nodes --
    which is what keeps public reads public without a special case.
    """
    required = CATALOGUE[node] if isinstance(node, str) else node

    async def check(request: Request, caller: Annotated[Caller, Depends(current_caller)]) -> Caller:
        permissions = request.app.state.runtime.services.permissions
        if not await caller_allows(permissions, caller, required):
            raise AuthenticationError if caller.kind == "anonymous" else AuthorizationError
        return caller

    return check


async def caller_allows(
    permissions: PermissionService,
    caller: Caller,
    node: PermissionNode,
) -> bool:
    """Whether an HTTP caller may exercise `node`.

    Both halves of AWS's permissions-boundary rule: the credential must carry the
    node, and the account behind it must hold it. Revoking someone's node
    therefore defangs every key they issued, without touching the keys.

    A credential with no owner falls back to its own nodes, since there is nobody
    to intersect with -- that is the machine-to-machine case, and the alternative
    would silently make such a credential inert.
    """
    if not credential_allows(caller, node):
        return False
    if caller.kind == "service" and caller.account_id is None:
        return True
    return await permissions.allows(subject_for(caller), node)


def credential_allows(caller: Caller, node: PermissionNode) -> bool:
    """Whether the credential itself carries `node`, before its owner is consulted."""
    return any(pattern.matches(node) for pattern in caller.nodes)


def subject_for(caller: Caller) -> Subject:
    """The permission subject behind an HTTP credential.

    `guild_id` is always None, so only global-scoped rules can ever apply: a
    grant made inside one Discord server must not authorize an HTTP call. The
    asymmetry is deliberate and documented in the API reference.
    """
    return Subject(account_id=caller.account_id, guild_id=None)


async def current_caller(
    request: Request,
    authorization: Annotated[str | None, Security(_authorization)],
) -> Caller:
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
        # Demoted from "every capability, forever" to an explicit list. It also
        # carries no account, so it is bounded by the anonymous subject's
        # authority as well -- it can no longer act as a person.
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


def require_consented_account(caller: Caller) -> int:
    """The account id behind a caller that may be written about, or the reason it may not.

    The single spelling of the write gate, after five routers had each grown their own copy and
    started to disagree about which of `kind`, `account_id` and `discord_id` they tested. Keyed
    on `account_id` alone: the `kind` test was redundant with it, and the `discord_id` test
    refused a CLI device and a Minecraft player who each hold a perfectly good account.

    Stays a plain function rather than only a dependency because three call sites need their own
    checks around it and cannot express that as `Depends`.
    """
    if caller.account_id is None:
        raise AuthenticationError
    if caller.consent_pending:
        raise ConsentRequiredError(account_id=caller.account_id).with_context(
            public_context={"consent_url": "/v1/users/me/consent", "notice_url": "/v1/consent/notice"},
            end_user_action="Accept the current privacy notice and retry.",
        )
    return caller.account_id


async def consented_account_id(caller: Annotated[Caller, Depends(current_caller)]) -> int:
    """Dependency form, for routes whose only precondition is a consented account."""
    return require_consented_account(caller)


ConsentedAccountId = Annotated[int, Depends(consented_account_id)]
"""A signed-in account that has accepted the current notice."""
