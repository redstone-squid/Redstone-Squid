"""Let clients retry a mutation without running it again.

An Idempotency-Key identifies a request within its caller's namespace. The route
dependency reserves that key before the handler runs; the middleware stores the
response before sending it. A later request with the same key and fingerprint
receives the stored response. The fingerprint includes the original body bytes,
so clients must resend the same representation, not merely equivalent JSON.
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID, uuid4

from anyio import CancelScope
from fastapi import Depends, Header, Request
from fastapi.applications import FastAPI
from fastapi.routing import APIRoute
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squid.api.security import Caller, current_caller
from squid.idempotency import IdempotencyService, PendingRequest, StoredResponse, UnsafeHttpMethod

_UNSAFE_METHODS = frozenset(UnsafeHttpMethod)
_REPLAYED_HEADERS = frozenset({"cache-control", "content-language", "content-type", "etag", "location", "pragma"})
_STATE_KEY = "squid_idempotency"
MAX_IDEMPOTENT_RESPONSE_BYTES = 1024 * 1024
"""Largest mutation response retained in memory and encrypted for replay."""

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r"^[\x21-\x7e]+$",
        description="Deduplicate an equivalent mutation in its server-derived caller namespace for 24 hours.",
    ),
]


class IdempotencyReplay(Exception):
    """Short-circuit an equivalent request with its stored response."""

    def __init__(self, response: StoredResponse) -> None:
        self.response = response
        super().__init__("Replay a completed idempotent request.")


@dataclass(frozen=True, slots=True)
class IdempotencyReservationState:
    """Reservation handed from the authenticated dependency to response completion."""

    service: IdempotencyService
    request: PendingRequest
    recoverable: bool = False


async def reserve_idempotent_request(
    request: Request,
    caller: Annotated[Caller, Depends(current_caller)],
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Reserve a caller key when an unsafe request supplies one."""
    await reserve_idempotent_request_for(request, caller.subject, idempotency_key)


async def reserve_edit_request(
    request: Request,
    caller: Annotated[Caller, Depends(current_caller)],
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Resume unfinished requests only for routes whose every write uses EditExecutor."""
    await reserve_idempotent_request_for(request, caller.subject, idempotency_key, recoverable=True)


async def reserve_idempotent_request_for(
    request: Request,
    caller: str,
    idempotency_key: str | None,
    *,
    recoverable: bool = False,
) -> None:
    """Reserve a mutation key, or raise IdempotencyReplay for a completed matching request.

    Read-only methods and requests without a key are ignored. New reservations
    are attached to request.state for the response-completion middleware.
    """
    if request.method not in _UNSAFE_METHODS or idempotency_key is None:
        return
    service = cast(IdempotencyService, request.app.state.runtime.services.idempotency)
    route = request.scope.get("route")
    route_path = cast(str, getattr(route, "path", request.url.path))
    reservation = await service.reserve(
        caller=caller,
        key=idempotency_key,
        fingerprint=await _request_fingerprint(request, route_path),
        method=UnsafeHttpMethod(request.method),
        route=route_path,
        recoverable=recoverable,
    )
    if isinstance(reservation, StoredResponse):
        raise IdempotencyReplay(reservation)
    setattr(request.state, _STATE_KEY, IdempotencyReservationState(service, reservation, recoverable))


def edit_operation_id(request: Request) -> UUID:
    """Share the reserved HTTP mutation identity with the application's outcome record."""
    reservation = getattr(request.state, _STATE_KEY, None)
    return reservation.request.request_id if isinstance(reservation, IdempotencyReservationState) else uuid4()


async def replay_response(_request: Request, error: Exception) -> Response:
    """Render a response stored by a completed equivalent request."""
    if not isinstance(error, IdempotencyReplay):
        raise error
    return Response(
        content=error.response.body,
        status_code=error.response.status_code,
        headers=dict(error.response.headers),
    )


class CompleteIdempotentResponseMiddleware:
    """Store a reserved request's response before sending it to the client.

    Responses without a reservation pass through unchanged. Reserved responses
    are buffered up to the configured limit; exceeding it raises RuntimeError.
    Completion is shielded from cancellation so a successful mutation can still
    retain its response after the client disconnects.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        buffered: list[Message] = []
        buffered_body_bytes = 0
        response_limit = _response_limit(scope)

        async def capture(message: Message) -> None:
            nonlocal buffered_body_bytes
            state = scope.get("state", {})
            if _STATE_KEY not in state:
                await send(message)
                return
            if message["type"] == "http.response.body":
                buffered_body_bytes += len(cast(bytes, message.get("body", b"")))
                if buffered_body_bytes > response_limit:
                    msg = f"An idempotent response exceeds the {response_limit}-byte replay limit."
                    raise RuntimeError(msg)
            buffered.append(message)

        await self.app(scope, receive, capture)
        state = scope.get("state", {})
        pending = state.get(_STATE_KEY)
        if not isinstance(pending, IdempotencyReservationState):
            return
        response = _stored_response(buffered, max_bytes=response_limit)
        # A disconnect or server shutdown must not interrupt completion after the
        # application mutation has succeeded. No bytes reach the caller until the
        # durable response commit settles.
        if not pending.recoverable or response.status_code < 500:
            with CancelScope(shield=True):
                stored = await pending.service.complete(pending.request, response)
            if stored != response:
                await Response(content=stored.body, status_code=stored.status_code, headers=dict(stored.headers))(
                    scope, receive, send
                )
                return
        # An uncertain edit or failed refresh stays resumable. The executor's
        # operation lock prevents a retry from racing an unsettled transaction.
        for message in buffered:
            await send(message)


def assert_idempotency_completion_installed(app: FastAPI) -> None:
    """Fail application construction when reservation has no completion middleware."""
    if not any(middleware.cls is CompleteIdempotentResponseMiddleware for middleware in app.user_middleware):
        msg = "Idempotent routes require CompleteIdempotentResponseMiddleware."
        raise RuntimeError(msg)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if not dependencies.intersection({reserve_idempotent_request, reserve_edit_request}):
            continue
        response_class = route.response_class
        if isinstance(response_class, type) and issubclass(response_class, StreamingResponse):
            msg = f"Idempotent route {route.path} cannot declare a streaming response."
            raise TypeError(msg)


async def _request_fingerprint(request: Request, route_path: str) -> bytes:
    """Hash request meaning, including a supplied revision precondition.

    Length prefixes separate the components. Bodies and query strings are hashed
    as received, so semantically equivalent JSON with different spacing differs.
    """
    digest = hashlib.sha256()
    components = [
        request.method.encode(),
        route_path.encode(),
        request.url.path.encode(),
        request.scope.get("query_string", b""),
        request.headers.get("content-type", "").encode(),
        await request.body(),
    ]
    if (if_match := request.headers.get("if-match")) is not None:
        components.extend((b"If-Match", if_match.encode()))
    for value in components:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.digest()


def _response_limit(scope: Scope) -> int:
    app = scope.get("app")
    config = getattr(getattr(app, "state", None), "config", None)
    return cast(int, getattr(getattr(config, "api", None), "idempotency_max_response_bytes", None)) or (
        MAX_IDEMPOTENT_RESPONSE_BYTES
    )


def _stored_response(messages: list[Message], *, max_bytes: int) -> StoredResponse:
    start = next((message for message in messages if message["type"] == "http.response.start"), None)
    if start is None:
        msg = "An idempotent request completed without starting an HTTP response."
        raise RuntimeError(msg)
    headers = tuple(
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in start["headers"]
        if name.decode("latin-1").casefold() in _REPLAYED_HEADERS
    )
    body = b"".join(
        cast(bytes, message.get("body", b"")) for message in messages if message["type"] == "http.response.body"
    )
    if len(body) > max_bytes:
        msg = f"An idempotent response exceeds the {max_bytes}-byte replay limit."
        raise RuntimeError(msg)
    return StoredResponse(status_code=start["status"], headers=headers, body=body)
