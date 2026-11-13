"""Bound request buffering before validation and idempotency fingerprinting."""

import re
from collections.abc import Callable, Sequence
from http import HTTPStatus

from litestar import Request
from litestar.exceptions import HTTPException
from litestar.routes import BaseRoute, HTTPRoute
from litestar.types import ASGIApp, Receive, ReceiveMessage, Scope, Send

from squid.api.errors import handle_http_error

DEFAULT_MAX_REQUEST_BODY_BYTES = 256 * 1024
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_STREAMS_OWN_BODY = "__squid_streams_own_body__"


def streams_own_body[EndpointT: Callable[..., object]](endpoint: EndpointT) -> EndpointT:
    """Exempt one endpoint from buffering, because it bounds its own stream.

    Apply this decorator to endpoints that enforce their own upload limits.
    The middleware reads this marker from the matched handler, so moving an
    endpoint does not require a second path list to be updated.
    """
    setattr(endpoint, _STREAMS_OWN_BODY, True)
    return endpoint


class BoundedRequestBodyMiddleware:
    """Buffer mutation request bodies up to a limit, rejecting larger bodies with 413.

    Content-Length can reject a request early, but streamed bytes are counted too.
    Accepted ASGI messages are replayed to downstream consumers. Paths marked by
    `streams_own_body` bypass buffering and must enforce their own limits.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        routes: Sequence[BaseRoute] | None = None,
        max_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    ) -> None:
        if max_bytes < 1:
            msg = "request body limit must be positive"
            raise ValueError(msg)
        self._app = app
        self._max_bytes = max_bytes
        self._exempt = tuple(
            re.compile(re.sub(r"\\{[^}]+\\}", "[^/]+", re.escape(route.path_format)))
            for route in routes or ()
            if isinstance(route, HTTPRoute)
            and any(getattr(handler.fn, _STREAMS_OWN_BODY, False) for handler, _ in route.route_handler_map.values())
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._must_bound(scope):
            await self._app(scope, receive, send)
            return
        if self._declared_length_exceeds(scope) or (messages := await self._read_bounded(receive)) is None:
            await self._reject(scope, receive, send)
            return

        next_message = 0

        async def replay_receive() -> ReceiveMessage:
            nonlocal next_message
            if next_message < len(messages):
                message = messages[next_message]
                next_message += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)

    def _must_bound(self, scope: Scope) -> bool:
        # Litestar resolves the native handler before entering route middleware.
        handler = scope.get("route_handler")
        if handler is not None and getattr(handler.fn, _STREAMS_OWN_BODY, False):
            return False
        return (
            scope["type"] == "http"
            and scope.get("method") in _UNSAFE_METHODS
            and not any(pattern.fullmatch(scope.get("path", "")) for pattern in self._exempt)
        )

    def _declared_length_exceeds(self, scope: Scope) -> bool:
        for name, raw_value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            try:
                if int(raw_value) > self._max_bytes:
                    return True
            except ValueError:
                continue
        return False

    async def _read_bounded(self, receive: Receive) -> list[ReceiveMessage] | None:
        messages: list[ReceiveMessage] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                return messages
            if message["type"] != "http.request":
                continue
            received += len(message.get("body", b""))
            if received > self._max_bytes:
                return None
            if not message.get("more_body", False):
                return messages

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = HTTPException(
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail=f"Request bodies are limited to {self._max_bytes} bytes.",
        )
        request = Request(scope, receive=receive)
        response = handle_http_error(request, error)
        await response.to_asgi_response(request.app, request)(scope, receive, send)
