"""Discord REST adapter for resolving current guild-member vote facts."""

import asyncio
import logging
from collections.abc import Callable
from time import monotonic
from typing import Any, Protocol, override

import aiohttp
from discord.errors import DiscordException, Forbidden, NotFound
from discord.http import HTTPClient, Route

from squid.permissions.application.ports import ActorCapabilityResolver
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.voting.domain import VoteActor, VoteKind
from squid.voting.errors import DiscordMemberServiceUnavailableError

logger = logging.getLogger(__name__)
DEFAULT_DISCORD_API_URL = Route.BASE
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
"""Ceiling on how long one lookup may wait out a rate limit.

discord.py raises `RateLimited` rather than sleeping past this, which is the
supported expression of the bound the hand-rolled limiter used to enforce with a
0-60s clamp on the body's `retry_after`.
"""
type Clock = Callable[[], float]


class DiscordMemberClient(Protocol):
    """The two operations this adapter needs from a Discord HTTP client.

    Narrower than `HTTPClient`, which satisfies it structurally: the adapter
    depends on the member lookup and on shutdown, not on the hundred other routes
    discord.py exposes.
    """

    async def get_member(self, guild_id: int, member_id: int) -> Any: ...

    async def close(self) -> None: ...


def rebased_url(base: str, url: str) -> str:
    """Point a route's URL at `base` instead of Discord's public API."""
    return url if base == Route.BASE else base + url.removeprefix(Route.BASE)


class _RebasedHTTPClient(HTTPClient):
    """`HTTPClient` pointed at a configured API base.

    `Route` builds its URL from a class attribute, so a configured loopback
    upstream cannot be expressed by construction. Rewriting the prefix on the way
    through `request` reaches every route, including the one `static_login` uses,
    and leaves rate limiting untouched: buckets are keyed by `route.key` and the
    major parameters, neither of which mentions the host.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        base: str,
        max_ratelimit_timeout: float | None = None,
    ) -> None:
        super().__init__(loop, max_ratelimit_timeout=max_ratelimit_timeout)
        self._base = base.rstrip("/")

    @override
    async def request(self, route: Route, **kwargs: Any) -> Any:
        route.url = rebased_url(self._base, route.url)
        return await super().request(route, **kwargs)


class DiscordRestActorResolver:
    """Resolve vote actors through discord.py's rate-limited HTTP client.

    This used to hand-roll a limiter that understood exactly one 429: retry once,
    sleep on the body's `retry_after`, raise on the second. It never read
    `X-RateLimit-Remaining`/`Reset-After`, never told a global limit from a bucket
    limit, and held no lock, so N concurrent votes in one guild fired N
    independent requests into the same bucket. `discord.http.HTTPClient` already
    does proactive per-bucket accounting keyed by route and major parameters,
    holds a global lock, and raises typed errors -- and it is already a hard
    dependency of this project.

    No gateway connection and no second `discord.Client`: `static_login` costs one
    `GET /users/@me`, and `get_member` returns the payload's `roles`, which is all
    a vote actor needs.
    """

    def __init__(
        self,
        bot_token: str,
        *,
        capabilities: ActorCapabilityResolver | None = None,
        http: DiscordMemberClient | None = None,
        cache_ttl_seconds: float = 300,
        clock: Clock = monotonic,
        api_url: str = DEFAULT_DISCORD_API_URL,
    ) -> None:
        self._token = bot_token
        self._capabilities = capabilities
        self._http = http
        self._owns_http = http is None
        self._closed = False
        self._api_url = api_url
        self._login_lock = asyncio.Lock()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, VoteActor | None]] = {}

    async def member(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return current member facts, raising when Discord cannot answer reliably."""
        del kind  # Every kind's nodes resolve together, so one load answers all of them.
        cache_key = (guild_id, discord_id)
        cached = self._cache.get(cache_key)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]

        payload = await self._request_member(discord_id, guild_id)
        actor = None if payload is None else await self._actor_from_payload(payload, account_id, discord_id, guild_id)
        self._cache[cache_key] = (now + self._cache_ttl_seconds, actor)
        return actor

    async def resolve(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Resolve refresh facts, retaining cached vote weight on any failure."""
        try:
            return await self.member(account_id, discord_id, guild_id, kind)
        except Exception:
            logger.warning(
                "Could not refresh Discord membership facts for vote session",
                exc_info=True,
                extra={"squid.discord.guild_id": guild_id},
            )
            return None

    async def aclose(self) -> None:
        """Close the internally-owned HTTP client, once."""
        self._closed = True
        if self._owns_http and self._http is not None:
            http, self._http = self._http, None
            await http.close()

    async def _client(self) -> DiscordMemberClient:
        """Return the logged-in client, opening the session on first use.

        Login is lazy rather than done at construction because the service graph
        builds synchronously; a deployment that never resolves a vote actor
        therefore never opens a Discord session at all.
        """
        if self._http is not None:
            return self._http
        if self._closed:
            msg = "The Discord member resolver is shut down."
            raise DiscordMemberServiceUnavailableError(msg)
        async with self._login_lock:
            if self._http is None:
                http = _RebasedHTTPClient(
                    asyncio.get_running_loop(),
                    base=self._api_url,
                    max_ratelimit_timeout=MAX_RATE_LIMIT_WAIT_SECONDS,
                )
                await http.static_login(self._token)
                self._http = http
            return self._http

    async def _request_member(self, discord_id: int, guild_id: int) -> object | None:
        """Fetch a member payload, or `None` when the member is not visible."""
        try:
            http = await self._client()
            return await http.get_member(guild_id, discord_id)
        except Forbidden, NotFound:
            # Not a member of the guild, or not visible to this token. Both are
            # "no such voter here", which is a fact rather than a failure.
            return None
        except DiscordException as error:
            # Covers RateLimited past the cap, 5xx, and a rejected login, none of
            # which let this lookup answer truthfully.
            raise self._unavailable(guild_id, error) from error
        except (TimeoutError, aiohttp.ClientError, OSError) as error:
            raise self._unavailable(guild_id, error) from error

    async def _actor_from_payload(self, payload: object, account_id: int, discord_id: int, guild_id: int) -> VoteActor:
        """Build an actor from the member payload, capabilities included.

        This used to hardcode both capability flags to False, so a `delete_log`
        vote cast over REST was always rejected as ineligible. The node is
        grantable to a Discord role, so the role ids already in this payload
        answer it -- no extra guild-permission fetch, and the Manage-Server
        bridge, which is the one source not represented here, is the
        lowest-priority one anyway.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
            raise self._malformed_member(guild_id)
        try:
            role_ids = frozenset(int(role_id) for role_id in payload["roles"])
        except (TypeError, ValueError) as error:
            raise self._malformed_member(guild_id) from error
        capabilities: frozenset[str] = frozenset()
        if self._capabilities is not None:
            capabilities = await self._capabilities.capabilities_for(
                account_id=account_id,
                discord_role_ids=role_ids,
                guild_id=guild_id,
                nodes=(VOTE_LOG_DELETE_CAST.name, VOTE_WEIGHT_STAFF.name, VOTE_POLL_CLOSE_ANY.name),
            )
        return VoteActor(account_id, discord_id, guild_id, role_ids, capabilities=capabilities)

    @staticmethod
    def _malformed_member(guild_id: int) -> DiscordMemberServiceUnavailableError:
        msg = "Discord returned malformed guild membership information."
        return DiscordMemberServiceUnavailableError(msg, context={"guild_id": guild_id})

    @staticmethod
    def _unavailable(guild_id: int, error: Exception) -> DiscordMemberServiceUnavailableError:
        msg = "Discord member lookup failed."
        return DiscordMemberServiceUnavailableError(
            msg,
            context={"guild_id": guild_id, "error_type": type(error).__name__},
        )
