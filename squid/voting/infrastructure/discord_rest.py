"""Discord REST adapter for resolving current guild-member vote facts."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
"""Maximum rate-limit delay accepted by the HTTP client before it raises `RateLimited`."""

_UNREADABLE = object()
"""Marks a guild this token cannot read, as opposed to one holding no such member."""
type Clock = Callable[[], float]


class VoterDiscordIdLookup(Protocol):
    """The account-to-snowflake read this adapter needs to reach Discord at all."""

    async def discord_id_for(self, account_id: int) -> int | None: ...


class DiscordMemberClient(Protocol):
    """Member lookup and shutdown operations supplied by a Discord HTTP client."""

    # Returns an awaitable rather than being declared `async`: discord.py's `HTTPClient.get_member`
    # is a plain method handing back the coroutine `request` produced, which satisfies this
    # structurally but not the stricter `CoroutineType` an `async def` in a protocol asks for.
    def get_member(self, guild_id: int, member_id: int) -> Awaitable[Any]: ...

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
    """Resolve an account's Discord membership for ballot eligibility and weighting.

    The account supplies a Discord identity; a member lookup supplies its guild
    roles, which the permission resolver uses to determine voting capabilities.
    Requests use discord.py's HTTP client for shared rate-limit accounting without
    opening a gateway connection.

    Member facts and resolved capabilities are cached per guild and Discord user
    for `cache_ttl_seconds`. An internally created client logs in lazily and is
    closed by `aclose`; injected clients remain the caller's responsibility.

    `member` exposes lookup failures to interactive callers. `resolve` converts
    failures to None so weight refreshes can preserve their cached values.
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
        """Return cached or fetched membership facts for an interactive request.

        Missing identities, absent members, and unreadable guilds return None. Other
        lookup failures raise `DiscordMemberServiceUnavailableError`.
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
        """Return membership for a weight refresh, or None when the lookup cannot answer.

        A known non-member becomes an actor without roles or capabilities. The
        weighting policy may give that actor a default weight or reject them as
        ineligible. An unreadable guild or failed member lookup returns None,
        allowing the service to preserve the ballot's cached weight.
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
        """Close the internally-owned HTTP client, once."""
        self._closed = True
        if self._owns_http and self._http is not None:
            http, self._http = self._http, None
            await http.close()

    async def _discord_id(self, account_id: int) -> int | None:
        """Look up the Discord identity for an account, without using the member cache."""
        if self._discord_ids is None:
            return None
        return await self._discord_ids.discord_id_for(account_id)

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
        """Validate member roles and resolve voting capabilities from account and role grants.

        The member payload does not establish Manage Server or bot-owner status, so
        those permission shortcuts are not enabled here.
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
