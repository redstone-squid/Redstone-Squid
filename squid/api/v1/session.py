"""Browser session snapshot used by server-rendered pages."""

from dataclasses import dataclass
from uuid import UUID

from litestar import Request, delete, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, JSONBody, SkipValidation
from whenever import Instant

from squid.api.contract import ANONYMOUS, WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import CurrentCaller, Services
from squid.api.idempotency import IdempotencyKey, reserve_idempotent_request_for
from squid.api.private_responses import private_response
from squid.api.security import requires
from squid.api.v1.browser_views import BrowserViews, SessionSnapshot
from squid.core.accounts.errors import AccountNotFoundError
from squid.core.permissions.domain.catalogue import ACCOUNT_IMPERSONATE


@get("/session", operation_id="browser_session_get", security=[ANONYMOUS, WEB], opt=contract(cli=browser_only()))
@private_response
async def session_snapshot(caller: CurrentCaller, services: Services) -> SessionSnapshot:
    """Return user, global permissions and header counts for the current browser."""
    return await BrowserViews(services, caller).session()


@dataclass
class StartImpersonation:
    account_id: UUID
    reason: str


async def reserve_session_change(
    request: Request, caller: CurrentCaller, idempotency_key: IdempotencyKey = None
) -> None:
    await reserve_idempotent_request_for(request, caller, idempotency_key, namespace=caller.browser_binding())


@post(
    "/impersonation",
    operation_id="browser_impersonation_start",
    security=[WEB_WRITE],
    guards=[requires(ACCOUNT_IMPERSONATE)],
    dependencies={"_reservation": Provide(reserve_session_change)},
    opt=contract(cli=browser_only(), scopes=(ACCOUNT_IMPERSONATE.name,)),
)
@private_response
async def start_impersonation(
    data: JSONBody[StartImpersonation],
    caller: CurrentCaller,
    services: Services,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> UUID:
    """Start debugging as an account. Send the returned UUID in Squid-Impersonation-Session."""
    binding = caller.browser_binding()
    target_id = await services.accounts.resolve_public_id(data.account_id, context=caller.context())
    session = await services.impersonation.start(binding, target_id, data.reason, context=caller.context())
    return session.id


@dataclass(frozen=True, slots=True)
class ImpersonationDetails:
    account_id: UUID
    reason: str
    expires_at: Instant
    revoked_at: Instant | None


@get(
    "/impersonation/{session_id:uuid}",
    operation_id="browser_impersonation_get",
    security=[WEB],
    opt=contract(cli=browser_only()),
)
@private_response
async def inspect_impersonation(
    session_id: FromPath[UUID], caller: CurrentCaller, services: Services
) -> ImpersonationDetails:
    """Inspect a session belonging to the current browser login."""
    session = await services.impersonation.inspect(caller.browser_binding(), session_id, context=caller.context())
    target = await services.accounts.get_account_by_id(session.target_id)
    if target is None:
        raise AccountNotFoundError(session.target_id)
    return ImpersonationDetails(target.public_account_id, session.reason, session.expires_at, session.revoked_at)


@delete(
    "/impersonation/{session_id:uuid}",
    operation_id="browser_impersonation_stop",
    dependencies={"_reservation": Provide(reserve_session_change)},
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
)
async def stop_impersonation(
    session_id: FromPath[UUID],
    caller: CurrentCaller,
    services: Services,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> None:
    """Revoke a debugging session belonging to the current browser login."""
    await services.impersonation.stop(caller.browser_binding(), session_id, context=caller.context())
