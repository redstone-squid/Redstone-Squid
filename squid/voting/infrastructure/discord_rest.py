"""Discord access for processes without a gateway connection, and the vote member lookup built on it.

The API and the worker don't connect to Discord's gateway, but they still need to read voters' guild
roles and recount the reactions on vote cards. `rest_client` gives them a discord.py `Client` that only
makes REST requests. With no gateway it has no cache, so it fetches every message and member it needs.

A process should create one client and share it between its adapters. discord.py tracks rate limits
per client, so separate clients would each hit Discord's limits without knowing about the others.
"""

import asyncio
import enum
import logging
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import Any, Protocol, override

import aiohttp
import discord
from discord.errors import DiscordException, Forbidden, NotFound
from discord.http import HTTPClient, Route
from discord.utils import MISSING

from squid.core.actors import Actor
from squid.permissions.application.ports import ActorPermissionChecker
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.voting.domain import VoteKind
from squid.voting.errors import DiscordMemberServiceUnavailableError

logger = logging.getLogger(__name__)
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

    @override
    async def request(self, route: Route, **kwargs: Any) -> Any:
        if self._closed:
            msg = "The Discord REST client is closed."
            raise DiscordRestClosedError(msg)
        # `static_login` sets the token before its own request, so that request skips this.
        if self.token is None:
            await self._login()
        route.url = rebased_url(self._base, route.url)
        return await super().request(route, **kwargs)

    @override
    async def close(self) -> None:
        self._closed = True
        await super().close()

    async def _login(self) -> None:
        async with self._login_lock:
            if self.token is not None:
                return
            try:
                # `Client.login` would also fetch the application's details, which no
                # adapter uses; logging the HTTP client in is all requests need.
                await self.static_login(self._bot_token)
            except BaseException:
                # `static_login` opened an aiohttp session; close it before the next attempt opens another.
                await super().close()
                raise


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


# Vote members


class VoterDiscordIdLookup(Protocol):
    """The account-to-snowflake read this adapter needs to reach Discord at all."""

    async def discord_id_for(self, account_id: int) -> int | None: ...


class _NotAMember(enum.Enum):
    """Why a member lookup produced no actor."""

    ABSENT = enum.auto()
    """Discord says this user is not in the guild."""
    UNKNOWN = enum.auto()
    """The bot cannot read the guild, so membership is unproven either way."""


type _Membership = Actor | _NotAMember


class DiscordRestActorResolver:
    """Resolve an account's Discord membership for ballot eligibility and weighting.

    The account supplies a Discord identity; a member lookup supplies its guild
    roles, which the permission resolver uses to determine voting capabilities.

    Lookups are cached per guild and Discord user for `cache_ttl_seconds`,
    including absent members and unreadable guilds.

    `member` exposes lookup failures to interactive callers. `resolve` converts
    failures to None so weight refreshes can preserve their cached values.
    """

    def __init__(
        self,
        client: discord.Client,
        *,
        permissions: ActorPermissionChecker | None = None,
        discord_ids: VoterDiscordIdLookup | None = None,
        cache_ttl_seconds: float = 300,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._client = client
        self._permissions = permissions
        self._discord_ids = discord_ids
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, _Membership]] = {}

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> Actor | None:
        """Return cached or fetched membership facts for an interactive request.

        Missing identities, absent members, and unreadable guilds return None. Other
        lookup failures raise `DiscordMemberServiceUnavailableError`.
        """
        del kind  # Every kind's nodes resolve together, so one lookup answers all of them.
        discord_id = await self._discord_id(account_id)
        if discord_id is None:
            return None
        membership = await self._membership(account_id, discord_id, guild_id)
        return membership if isinstance(membership, Actor) else None

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> Actor | None:
        """Return membership for a weight refresh, or None when the lookup cannot answer.

        A known non-member becomes an actor without roles or capabilities. The
        weighting policy may give that actor a default weight or reject them as
        ineligible. An unreadable guild or failed member lookup returns None,
        allowing the service to preserve the ballot's cached weight.
        """
        del kind
        try:
            discord_id = await self._discord_id(account_id)
            if discord_id is None:
                # No Discord identity means no guild membership and so no role weight.
                return Actor(account_id=account_id, discord_user_id=None, guild_id=guild_id)
            membership = await self._membership(account_id, discord_id, guild_id)
        except Exception:
            logger.warning(
                "Could not refresh Discord membership facts for vote session",
                exc_info=True,
                extra={"squid.discord.guild_id": guild_id},
            )
            return None
        match membership:
            case Actor():
                return membership
            case _NotAMember.ABSENT:
                return Actor(account_id=account_id, discord_user_id=discord_id, guild_id=guild_id)
            case _NotAMember.UNKNOWN:
                return None

    async def _discord_id(self, account_id: int) -> int | None:
        if self._discord_ids is None:
            return None
        return await self._discord_ids.discord_id_for(account_id)

    async def _membership(self, account_id: int, discord_id: int, guild_id: int) -> _Membership:
        """Return the cached lookup for this guild member, or fetch and cache a fresh one."""
        cache_key = (guild_id, discord_id)
        now = self._clock()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]
        membership = await self._fetch_membership(account_id, discord_id, guild_id)
        self._cache[cache_key] = (now + self._cache_ttl_seconds, membership)
        return membership

    async def _fetch_membership(self, account_id: int, discord_id: int, guild_id: int) -> _Membership:
        try:
            # The raw payload is enough: only the role IDs matter, and parsing a Member
            # would need a Guild object, which a gateway-less client does not have.
            payload = await self._client.http.get_member(guild_id, discord_id)
        except NotFound:
            return _NotAMember.ABSENT
        except Forbidden:
            return _NotAMember.UNKNOWN
        except DiscordRestClosedError as error:
            msg = "The Discord member resolver is shut down."
            raise DiscordMemberServiceUnavailableError(msg, context={"guild_id": guild_id}) from error
        except (DiscordException, TimeoutError, aiohttp.ClientError, OSError) as error:
            # DiscordException covers RateLimited past the cap, 5xx, and a rejected login,
            # none of which let this lookup answer truthfully.
            msg = "Discord member lookup failed."
            raise DiscordMemberServiceUnavailableError(
                msg, context={"guild_id": guild_id, "error_type": type(error).__name__}
            ) from error
        return await self._actor_from_payload(payload, account_id, discord_id, guild_id)

    async def _actor_from_payload(self, payload: object, account_id: int, discord_id: int, guild_id: int) -> Actor:
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
        actor = Actor(account_id=account_id, discord_user_id=discord_id, guild_id=guild_id, discord_role_ids=role_ids)
        if self._permissions is not None:
            capabilities = (
                await self._permissions.check(
                    actor, *(VOTE_LOG_DELETE_CAST.name, VOTE_WEIGHT_STAFF.name, VOTE_POLL_CLOSE_ANY.name)
                )
            ).allowed_nodes
            actor = replace(actor, capabilities=capabilities)
        return actor

    @staticmethod
    def _malformed_member(guild_id: int) -> DiscordMemberServiceUnavailableError:
        msg = "Discord returned malformed guild membership information."
        return DiscordMemberServiceUnavailableError(msg, context={"guild_id": guild_id})
