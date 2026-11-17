"""Server-side cache policy for owner-private HTTP resources."""

from collections.abc import Callable

from litestar import Router
from litestar.routes import HTTPRoute
from litestar.types import ASGIApp, Message, Receive, Scope, Send

# Endpoint and router opt metadata; routers protect unknown descendant paths too.
PRIVATE_RESPONSE = "x-squid-private-response"


def private_response[EndpointT: Callable[..., object]](endpoint: EndpointT) -> EndpointT:
    """Prevent storage for this endpoint and unknown descendants of its path."""
    setattr(endpoint, PRIVATE_RESPONSE, True)
    return endpoint


_CACHE_CONTROL = b"cache-control"
_PRAGMA = b"pragma"


class PrivateResponseHeadersMiddleware:
    """Prevent browsers and intermediaries from retaining private API responses."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._path_prefixes: tuple[str, ...] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._path_prefixes is None:
            prefixes: set[str] = set()
            for route in scope["app"].routes:
                if not isinstance(route, HTTPRoute):
                    continue
                for handler, _ in route.route_handler_map.values():
                    path = ""
                    for layer in handler.ownership_layers:
                        if isinstance(layer, Router):
                            path += layer.path.rstrip("/")
                        if layer.opt.get(PRIVATE_RESPONSE) or (
                            layer is handler and getattr(handler.fn, PRIVATE_RESPONSE, False)
                        ):
                            prefix = path if isinstance(layer, Router) else route.path_format.split("{", 1)[0]
                            prefixes.add(prefix.rstrip("/"))
            self._path_prefixes = tuple(sorted(prefixes))
        if scope["type"] != "http" or not self._is_private_path(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        async def prevent_storage(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value) for name, value in message["headers"] if name.lower() not in {_CACHE_CONTROL, _PRAGMA}
                ]
                headers.extend(((_CACHE_CONTROL, b"no-store"), (_PRAGMA, b"no-cache")))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, prevent_storage)

    def _is_private_path(self, path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in self._path_prefixes or ())
