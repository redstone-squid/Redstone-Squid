"""FastAPI integration for durable mutation replay."""

import hashlib
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squid.api.security import Principal, current_principal
from squid.idempotency import IdempotencyService, PendingRequest, StoredResponse

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REPLAYED_HEADERS = frozenset({"cache-control", "content-language", "content-type", "etag", "location", "pragma"})
_STATE_KEY = "squid_idempotency"


class IdempotencyReplay(Exception):
    """Short-circuit an equivalent request with its stored response."""

    def __init__(self, response: StoredResponse) -> None:
        self.response = response
        super().__init__("Replay a completed idempotent request.")


@dataclass(frozen=True, slots=True)
class _PendingResponse:
    service: IdempotencyService
    request: PendingRequest


async def enforce_request_idempotency(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[\x21-\x7e]+$",
            description="Deduplicate an equivalent mutation by this authenticated caller for 24 hours.",
        ),
    ] = None,
) -> None:
    """Reserve a caller key when an unsafe request supplies one."""
    if request.method not in _UNSAFE_METHODS or idempotency_key is None:
        return
    service = cast(IdempotencyService, request.app.state.runtime.services.idempotency)
    route = request.scope.get("route")
    route_path = cast(str, getattr(route, "path", request.url.path))
    reservation = await service.reserve(
        principal=principal.subject,
        key=idempotency_key,
        fingerprint=await _request_fingerprint(request, route_path),
        method=request.method,
        route=route_path,
    )
    if isinstance(reservation, StoredResponse):
        raise IdempotencyReplay(reservation)
    setattr(request.state, _STATE_KEY, _PendingResponse(service, reservation))


async def replay_response(_request: Request, error: Exception) -> Response:
    """Render a response stored by a completed equivalent request."""
    if not isinstance(error, IdempotencyReplay):
        raise error
    return Response(
        content=error.response.body,
        status_code=error.response.status_code,
        headers=dict(error.response.headers),
    )


class IdempotencyResponseMiddleware:
    """Buffer and durably complete responses for newly reserved requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        buffered: list[Message] = []

        async def capture(message: Message) -> None:
            state = scope.get("state", {})
            if _STATE_KEY not in state:
                await send(message)
                return
            buffered.append(message)

        await self.app(scope, receive, capture)
        state = scope.get("state", {})
        pending = state.get(_STATE_KEY)
        if not isinstance(pending, _PendingResponse):
            return
        response = _stored_response(buffered)
        await pending.service.complete(pending.request, response)
        for message in buffered:
            await send(message)


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


def _stored_response(messages: list[Message]) -> StoredResponse:
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
    return StoredResponse(status_code=start["status"], headers=headers, body=body)
