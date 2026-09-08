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
"""Ceiling on how long one lookup waits out a rate limit; discord.py raises `RateLimited` past it."""

_UNREADABLE = object()
"""Marks a guild this token cannot read, as opposed to one holding no such member."""

type Clock = Callable[[], float]


class VoterDiscordIdLookup(Protocol):
    """The account-to-snowflake read this adapter needs to reach Discord at all."""

    async def discord_id_for(self, account_id: int) -> int | None:
        """Return the account's Discord snowflake, or `None` when it has no Discord identity."""
        ...


class DiscordMemberClient(Protocol):
    """The two operations this adapter needs from a Discord HTTP client, one of them `close`.

    Narrower than `HTTPClient`, which satisfies it structurally: the adapter depends on the member
    lookup and on shutdown, not on the hundred other routes discord.py exposes.
    """

    async def get_member(self, guild_id: int, member_id: int) -> Any:
        """Return the raw member payload, raising `NotFound` when the guild holds no such member."""
        ...

    async def close(self) -> None:
        """End the underlying HTTP session; no route may be requested afterwards."""
        ...


def rebased_url(base: str, url: str) -> str:
    """Point a route's URL at `base` instead of Discord's public API."""
    return url if base == Route.BASE else base + url.removeprefix(Route.BASE)


class _RebasedHTTPClient(HTTPClient):
    """`HTTPClient` pointed at a configured API base.

    `Route` builds its URL from a class attribute, so a loopback upstream cannot be expressed by
    construction. Rewriting the prefix inside `request` reaches every route, `static_login`
    included, and leaves rate limiting untouched: buckets are keyed by `route.key` and the major
    parameters, neither of which mentions the host.
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
    """Resolve vote actors through discord.py's rate-limited HTTP client; `aclose` ends it.

    `HTTPClient` does proactive per-bucket accounting keyed by route and major parameters and holds
    a global lock, so concurrent votes in one guild share one bucket. No gateway connection and no
    second `discord.Client`: `static_login` costs one `GET /users/@me`, and `get_member` returns the
    payload's `roles`, which is all a vote actor needs.

    Member facts are cached per `(guild_id, discord_id)` for `cache_ttl_seconds`.
    """

    def __init__(
        self,
        bot_token: str,
        *,
        capabilities: ActorCapabilityResolver | None = None,
        discord_ids: VoterDiscordIdLookup | None = None,
        http: DiscordMemberClient | None = None,
        cache_ttl_seconds: float = 300,
        clock: Clock = monotonic,
        api_url: str = DEFAULT_DISCORD_API_URL,
    ) -> None:
        self._token = bot_token
        self._capabilities = capabilities
        self._discord_ids = discord_ids
        self._http = http
        self._owns_http = http is None
        self._closed = False
        self._api_url = api_url
        self._login_lock = asyncio.Lock()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, VoteActor | None, bool]] = {}

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return current member facts, or `None` for an absent member or an unreadable guild.

        Callers gate access on this, so an unreadable guild denies exactly as an absent member
        does. A transport failure raises `DiscordMemberServiceUnavailableError` instead.
        """
        actor, _ = await self._member_with_certainty(account_id, guild_id, kind)
        return actor

    async def _member_with_certainty(
        self, account_id: int, guild_id: int, kind: VoteKind
    ) -> tuple[VoteActor | None, bool]:
        """Return member facts plus whether an absence is a fact or an unanswered question."""
        del kind  # Every kind's nodes resolve together, so one load answers all of them.
        discord_id = await self._discord_id(account_id)
        if discord_id is None:
            # No Discord identity means no guild membership and so no role weight.
            return None, True
        cache_key = (guild_id, discord_id)
        cached = self._cache.get(cache_key)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1], cached[2]

        payload = await self._request_member(discord_id, guild_id)
        certain = payload is not _UNREADABLE
        actor = (
            await self._actor_from_payload(payload, account_id, discord_id, guild_id)
            if payload is not None and certain
            else None
        )
        self._cache[cache_key] = (now + self._cache_ttl_seconds, actor, certain)
        return actor, certain

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Resolve refresh facts, retaining the cached vote weight on any failure.

        A provably absent member resolves to an actor holding nothing, so the refresh drops them to
        the default weight; an unreadable guild resolves to `None`, which keeps the recorded weight.
        """
        try:
            actor, certain = await self._member_with_certainty(account_id, guild_id, kind)
        except Exception:
            logger.warning(
                "Could not refresh Discord membership facts for vote session",
                exc_info=True,
                extra={"squid.discord.guild_id": guild_id},
            )
            return None
        if actor is not None:
            return actor
        if not certain:
            return None
        return VoteActor(account_id, await self._discord_id(account_id) or 0, guild_id)

    async def aclose(self) -> None:
        """Close the HTTP client this resolver opened; a later lookup raises rather than logging in again."""
        self._closed = True
        if self._owns_http and self._http is not None:
            http, self._http = self._http, None
            await http.close()

    async def _discord_id(self, account_id: int) -> int | None:
        """The snowflake behind a voting account.

        The member cache is keyed on the answer rather than the question, so this lookup is not
        covered by it and a refresh costs one query per actor.
        """
        if self._discord_ids is None:
            return None
        return await self._discord_ids.discord_id_for(account_id)

    async def _client(self) -> DiscordMemberClient:
        """Return the logged-in client, opening the session on first use.

        Login is lazy because the service graph builds synchronously, so a deployment that never
        resolves a vote actor never opens a Discord session. Raises
        `DiscordMemberServiceUnavailableError` once `aclose` has run.
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
        """Fetch a member payload, `None` when absent, or `_UNREADABLE` when unknowable."""
        try:
            http = await self._client()
            return await http.get_member(guild_id, discord_id)
        except NotFound:
            # Discord answered: there is no such voter here.
            return None
        except Forbidden:
            # This token cannot see the guild, so absence is unproven.
            return _UNREADABLE
        except DiscordException as error:
            # Covers RateLimited past the cap, 5xx, and a rejected login, none of
            # which let this lookup answer truthfully.
            raise self._unavailable(guild_id, error) from error
        except (TimeoutError, aiohttp.ClientError, OSError) as error:
            raise self._unavailable(guild_id, error) from error

    async def _actor_from_payload(self, payload: object, account_id: int, discord_id: int, guild_id: int) -> VoteActor:
        """Build an actor from the member payload, capabilities included.

        Voting nodes are grantable to Discord roles, so the role ids in this payload answer them
        without a second guild-permission fetch. The Manage-Server bridge is the one capability
        source not represented here, and is the lowest-priority one. A payload without a list of
        integer role ids raises `DiscordMemberServiceUnavailableError`.
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
