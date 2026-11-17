"""One lazy REST-only discord.py client shared by a process's adapters.

The owner closes this client at shutdown. Gateway state is never opened; the
HTTP implementation retains discord.py's bucket accounting and retry behavior.
"""

import asyncio
from typing import Any, override

import anyio
import discord
from discord.http import HTTPClient, Route
from discord.utils import MISSING

DEFAULT_DISCORD_API_URL = Route.BASE
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
"""Maximum rate-limit delay accepted by the HTTP client before it raises `RateLimited`."""


# Gateway-less client


class DiscordRestClosedError(RuntimeError):
    """A request was made after the REST client was closed."""


def rebased_url(base: str, url: str) -> str:
    """Point a route's URL at `base` instead of Discord's public API."""
    return url if base == Route.BASE else base + url.removeprefix(Route.BASE)


class _LazyLoginHTTPClient(HTTPClient):
    """`HTTPClient` that logs in on its first request and sends requests to a configured API base.

    Login waits for the first request because bootstrap builds services synchronously, outside
    the event loop, and a process that never calls Discord should not open a connection at all.
    If login fails, the next request tries again. After `close`, requests raise
    `DiscordRestClosedError` instead of logging in again.

    The base can't be set through discord.py: `Route` builds every URL from the class attribute
    `Route.BASE`. Rewriting the prefix in `request` covers every route, including the one login
    uses. Rate limiting is unaffected, because discord.py keys its buckets by route path and
    parameters, never by host.
    """

    def __init__(self, bot_token: str, *, base: str) -> None:
        # discord.py stores the loop without using it; `Client.login` would fill it in.
        super().__init__(MISSING, max_ratelimit_timeout=MAX_RATE_LIMIT_WAIT_SECONDS)
        self._bot_token = bot_token
        self._base = base.rstrip("/")
        self._login_lock = asyncio.Lock()
        self._closed = False
        self._ready = False
        self._login_task: asyncio.Task[Any] | None = None

    @override
    async def request(self, route: Route, **kwargs: Any) -> Any:
        if self._closed:
            msg = "The Discord REST client is closed."
            raise DiscordRestClosedError(msg)
        # static_login calls request recursively in its own task. Other callers
        # must wait even after it has assigned the token but before login finishes.
        if self._login_task is not asyncio.current_task():
            await self._login()
        if self._closed:
            message = "The Discord REST client is closed."
            raise DiscordRestClosedError(message)
        route.url = rebased_url(self._base, route.url)
        return await super().request(route, **kwargs)

    @override
    async def close(self) -> None:
        self._closed = True
        async with self._login_lock:
            await super().close()

    async def _login(self) -> None:
        async with self._login_lock:
            if self._closed:
                message = "The Discord REST client is closed."
                raise DiscordRestClosedError(message)
            if self._ready:
                return
            self._login_task = asyncio.current_task()
            try:
                # `Client.login` would also fetch the application's details, which no
                # adapter uses; logging the HTTP client in is all requests need.
                await self.static_login(self._bot_token)
                self._ready = True
            except BaseException:
                # `static_login` opened an aiohttp session; close it before the next attempt opens another.
                self.token = None
                with anyio.CancelScope(shield=True):
                    await super().close()
                raise
            finally:
                self._login_task = None


class RestOnlyClient(discord.Client):
    """A discord.py client that sends REST requests through `http` and never opens a gateway connection."""

    def __init__(self, http: HTTPClient) -> None:
        # Intents only shape a gateway connection, which this client never opens. The guilds
        # intent is set only because discord.py warns at construction when it is missing.
        super().__init__(intents=discord.Intents(guilds=True))
        # Client.__init__ builds its own HTTPClient and hands it to the ConnectionState
        # that models such as Message and Reaction send their requests through, so
        # both references must point at the replacement.
        self.http = self._connection.http = http


def rest_client(bot_token: str, *, api_url: str = DEFAULT_DISCORD_API_URL) -> RestOnlyClient:
    """Build a gateway-less client that logs in on its first request; the caller closes it."""
    return RestOnlyClient(_LazyLoginHTTPClient(bot_token, base=api_url))
