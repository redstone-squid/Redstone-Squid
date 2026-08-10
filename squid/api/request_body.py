"""Bound request buffering before validation and idempotency fingerprinting."""

import re
from http import HTTPStatus

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squid.api.errors import handle_http_error

DEFAULT_MAX_REQUEST_BODY_BYTES = 256 * 1024
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_BOUNDED_MEDIA_UPLOAD = re.compile(
    r"^/v1/submissions/drafts/[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/media/(?:image|video)$"
)


class BoundedRequestBodyMiddleware:
    """Reject oversized mutation bodies before downstream code can buffer them."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES) -> None:
        if max_bytes < 1:
            msg = "request body limit must be positive"
            raise ValueError(msg)
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._must_bound(scope):
            await self._app(scope, receive, send)
            return
        if self._declared_length_exceeds(scope) or (messages := await self._read_bounded(receive)) is None:
            await self._reject(scope, receive, send)
            return

        next_message = 0

        async def replay_receive() -> Message:
            nonlocal next_message
            if next_message < len(messages):
                message = messages[next_message]
                next_message += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)

    def _must_bound(self, scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") in _UNSAFE_METHODS
            and _BOUNDED_MEDIA_UPLOAD.fullmatch(scope.get("path", "")) is None
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

    async def _read_bounded(self, receive: Receive) -> list[Message] | None:
        messages: list[Message] = []
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
        response = await handle_http_error(Request(scope, receive=receive), error)
        await response(scope, receive, send)
