"""FastAPI integration for durable mutation replay."""

import hashlib
from dataclasses import dataclass
from typing import Annotated, cast

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


async def reserve_idempotent_request(
    request: Request,
    caller: Annotated[Caller, Depends(current_caller)],
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Reserve a caller key when an unsafe request supplies one."""
    await reserve_idempotent_request_for(request, caller.subject, idempotency_key)


async def reserve_idempotent_request_for(
    request: Request,
    caller: str,
    idempotency_key: str | None,
) -> None:
    """Reserve a key in a server-derived caller namespace."""
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
    )
    if isinstance(reservation, StoredResponse):
        raise IdempotencyReplay(reservation)
    setattr(request.state, _STATE_KEY, IdempotencyReservationState(service, reservation))


# Route declarations keep these compatibility names during the terminology transition.
enforce_request_idempotency = reserve_idempotent_request
enforce_request_idempotency_for = reserve_idempotent_request_for


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
    """Buffer and durably complete responses for newly reserved requests."""

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
        with CancelScope(shield=True):
            await pending.service.complete(pending.request, response)
        for message in buffered:
            await send(message)


IdempotencyResponseMiddleware = CompleteIdempotentResponseMiddleware


def assert_idempotency_completion_installed(app: FastAPI) -> None:
    """Fail application construction when reservation has no completion middleware."""
    if not any(middleware.cls is CompleteIdempotentResponseMiddleware for middleware in app.user_middleware):
        msg = "Idempotent routes require CompleteIdempotentResponseMiddleware."
        raise RuntimeError(msg)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if reserve_idempotent_request not in dependencies:
            continue
        response_class = route.response_class
        if isinstance(response_class, type) and issubclass(response_class, StreamingResponse):
            msg = f"Idempotent route {route.path} cannot declare a streaming response."
            raise TypeError(msg)


async def _request_fingerprint(request: Request, route_path: str) -> bytes:
    digest = hashlib.sha256()
    for value in (
        request.method.encode(),
        route_path.encode(),
        request.scope.get("query_string", b""),
        request.headers.get("content-type", "").encode(),
        await request.body(),
    ):
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
