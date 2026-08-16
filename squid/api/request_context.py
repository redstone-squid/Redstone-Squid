"""Request-scoped correlation: resolve, propagate, and echo a Request-Id header."""

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squid.observability import active_trace_id, bind_correlation_id, unbind_correlation_id

REQUEST_ID_HEADER = "Request-Id"

# Accept UUIDs (with dashes), 32-hex trace ids, and ULIDs, while rejecting whitespace, control
# characters, and absurd lengths so an untrusted inbound value cannot inject into logs or headers.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,128}")

# Minimal W3C traceparent: version 00, 32-hex trace id, 16-hex span id, 2-hex flags.
_TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}")


def _traceparent_trace_id(value: str | None) -> str | None:
    """Parse a W3C traceparent's trace id, used only when OpenTelemetry is inactive."""
    if value is None:
        return None
    match = _TRACEPARENT.fullmatch(value.strip())
    if match is None or match.group(1) == "0" * 32:
        return None
    return match.group(1)


def resolve_request_id(headers: Headers) -> str:
    """Resolve one correlation id for a request from untrusted inbound headers.

    Priority: a valid inbound ``Request-Id`` (echoed verbatim), then the active trace id, then a
    trace id parsed from ``traceparent`` (only reachable without the observability extra, since
    OpenTelemetry's instrumentation already surfaces the same value through ``active_trace_id``),
    then a freshly generated id. Accepted values are never truncated.
    """
    inbound = headers.get(REQUEST_ID_HEADER)
    if inbound is not None and _VALID_REQUEST_ID.fullmatch(inbound):
        return inbound
    trace_id = active_trace_id()
    if trace_id is not None:
        return trace_id
    parsed = _traceparent_trace_id(headers.get("traceparent"))
    if parsed is not None:
        return parsed
    return uuid4().hex


class RequestContextMiddleware:
    """Bind one correlation id per request and echo it as the Request-Id response header."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope))
        token = bind_correlation_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                del headers[REQUEST_ID_HEADER]
                headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        completed = False
        try:
            await self._app(scope, receive, send_with_request_id)
            completed = True
        finally:
            # Only reset on the normal path. When the app raises, Starlette's ServerErrorMiddleware
            # renders the 500 response *outside* this middleware and reads the same bound id; the
            # per-request task context is discarded afterwards, so the un-reset var cannot leak.
            if completed:
                unbind_correlation_id(token)
