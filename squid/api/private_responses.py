"""Server-side cache policy for owner-private HTTP resources."""

from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PRIVATE_API_PATH_PREFIXES = (
    "/v1/auth/csrf",
    "/v1/minecraft/auth",
    "/v1/submissions/drafts",
    "/v1/users/me",
    "/v1/vote-sessions",
)

_CACHE_CONTROL = b"cache-control"
_PRAGMA = b"pragma"


class PrivateResponseHeadersMiddleware:
    """Prevent browsers and intermediaries from retaining private API responses."""

    def __init__(self, app: ASGIApp, *, path_prefixes: Sequence[str] = PRIVATE_API_PATH_PREFIXES) -> None:
        self._app = app
        self._path_prefixes = tuple(path_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in self._path_prefixes)
