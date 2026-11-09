"""Discord REST adapters for vote member facts and vote card reactions, for processes without a gateway.

Both adapters send their requests through one `DiscordRestSession`, which holds a discord.py `Client` that
is logged in with the bot token but never connects to the gateway. Without the gateway the client has no
cache, so every message or member is fetched, but discord.py still parses the responses and pages through
results. discord.py tracks rate limits per `HTTPClient`, so sharing a single client keeps every request made
with the bot token in one rate-limit accounting.
"""

import asyncio
import contextlib
import enum
import logging
from collections.abc import Callable, Sequence
from time import monotonic
from typing import Any, Protocol, override

import aiohttp
import discord
from discord.errors import DiscordException, Forbidden, NotFound
from discord.http import HTTPClient, Route

from squid.permissions.application.ports import ActorCapabilityResolver
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.voting.application.recount import CardReactions
from squid.voting.domain import VoteActor, VoteKind, VoteMessage
from squid.voting.errors import DiscordMemberServiceUnavailableError

logger = logging.getLogger(__name__)
DEFAULT_DISCORD_API_URL = Route.BASE
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
"""Maximum rate-limit delay accepted by the HTTP client before it raises `RateLimited`."""


# Shared client


class DiscordRestSessionClosedError(RuntimeError):
    """A request was made after `DiscordRestSession.aclose`."""


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


class RestOnlyClient(discord.Client):
    """A discord.py client that sends REST requests through `http` and never opens a gateway connection."""

    def __init__(self, http: HTTPClient) -> None:
        super().__init__(intents=discord.Intents.none())
        # Client.__init__ builds its own HTTPClient and hands it to the ConnectionState
        # that models such as Message and Reaction send their requests through, so
        # both references must point at the replacement.
        self.http = self._connection.http = http


class DiscordRestSession:
    """A bot-token REST client that logs in on first use and is shared by the REST adapters.

    Login is lazy because the service graph is built synchronously, outside the event
    loop; a process that never makes a Discord request never opens a session. `aclose`
    closes the client this session created, and later requests raise
    `DiscordRestSessionClosedError` rather than logging in again.

    `wrapping` adopts an existing client instead. That client stays its owner's
    responsibility, so `aclose` leaves it open.
    """

    def __init__(self, bot_token: str, *, api_url: str = DEFAULT_DISCORD_API_URL) -> None:
        self._token = bot_token
        self._api_url = api_url
        self._client: discord.Client | None = None
        self._owns_client = True
        self._closed = False
        self._login_lock = asyncio.Lock()

    @classmethod
    def wrapping(cls, client: discord.Client) -> DiscordRestSession:
        """Use an already logged-in client, which `aclose` will not close."""
        session = cls("")
        session._client = client
        session._owns_client = False
        return session

    async def client(self) -> discord.Client:
        """Return the logged-in client, logging in on the first call."""
        if self._closed:
            msg = "The Discord REST session is shut down."
            raise DiscordRestSessionClosedError(msg)
        if self._client is not None:
            return self._client
        async with self._login_lock:
            if self._client is None:
                http = _RebasedHTTPClient(
                    asyncio.get_running_loop(),
                    base=self._api_url,
                    max_ratelimit_timeout=MAX_RATE_LIMIT_WAIT_SECONDS,
                )
                # `Client.login` would also fetch the application's details, which these
                # adapters never use; logging the HTTP client in is all requests need.
                await http.static_login(self._token)
                self._client = RestOnlyClient(http)
            return self._client

    async def aclose(self) -> None:
        """Close the client this session created, once."""
        self._closed = True
        client, self._client = self._client, None
        if self._owns_client and client is not None:
            await client.close()


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


type _Membership = VoteActor | _NotAMember


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
        session: DiscordRestSession,
        *,
        capabilities: ActorCapabilityResolver | None = None,
        discord_ids: VoterDiscordIdLookup | None = None,
        cache_ttl_seconds: float = 300,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._session = session
        self._capabilities = capabilities
        self._discord_ids = discord_ids
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, _Membership]] = {}

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return cached or fetched membership facts for an interactive request.

        Missing identities, absent members, and unreadable guilds return None. Other
        lookup failures raise `DiscordMemberServiceUnavailableError`.
        """
        del kind  # Every kind's nodes resolve together, so one lookup answers all of them.
        discord_id = await self._discord_id(account_id)
        if discord_id is None:
            return None
        membership = await self._membership(account_id, discord_id, guild_id)
        return membership if isinstance(membership, VoteActor) else None

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
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
                # 0 stands in for the snowflake the account does not have.
                return VoteActor(account_id, 0, guild_id)
            membership = await self._membership(account_id, discord_id, guild_id)
        except Exception:
            logger.warning(
                "Could not refresh Discord membership facts for vote session",
                exc_info=True,
                extra={"squid.discord.guild_id": guild_id},
            )
            return None
        match membership:
            case VoteActor():
                return membership
            case _NotAMember.ABSENT:
                return VoteActor(account_id, discord_id, guild_id)
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
            client = await self._session.client()
            # The raw payload is enough: only the role IDs matter, and parsing a Member
            # would need a Guild object, which a gateway-less client does not have.
            payload = await client.http.get_member(guild_id, discord_id)
        except NotFound:
            return _NotAMember.ABSENT
        except Forbidden:
            return _NotAMember.UNKNOWN
        except DiscordRestSessionClosedError as error:
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


# Vote cards


class DiscordRestVoteCards:
    """Read and change the reactions on vote cards through Discord's REST API.

    This implements `VoteCardReactions` for the worker. It mirrors the bot's gateway
    `DiscordVoteCards`, except that it always fetches the card because there is no message
    cache. Reading a card costs Discord requests in proportion to the number of voters.

    A card that is deleted or hidden reads as None. Other Discord failures, such as a rate
    limit longer than `MAX_RATE_LIMIT_WAIT_SECONDS` or an outage, propagate.
    """

    def __init__(self, session: DiscordRestSession) -> None:
        self._session = session

    async def read(self, card: VoteMessage, emojis: Sequence[str]) -> CardReactions | None:
        try:
            message = await (await self._message(card)).fetch()
        except NotFound, Forbidden:
            return None
        reactions = {str(reaction.emoji): reaction for reaction in message.reactions}
        voters: dict[str, list[int]] = {}
        for emoji in emojis:
            reaction = reactions.get(emoji)
            if reaction is not None:
                voters[emoji] = [user.id async for user in reaction.users(limit=None) if not user.bot]
        own = frozenset(emoji for emoji in emojis if (reaction := reactions.get(emoji)) is not None and reaction.me)
        return CardReactions(voters, own)

    async def remove(self, card: VoteMessage, emoji: str, user_id: int) -> None:
        message = await self._message(card)
        with contextlib.suppress(NotFound, Forbidden):
            await message.remove_reaction(emoji, discord.Object(user_id))

    async def add_own(self, card: VoteMessage, emoji: str) -> None:
        message = await self._message(card)
        with contextlib.suppress(NotFound, Forbidden):
            await message.add_reaction(emoji)

    async def _message(self, card: VoteMessage) -> discord.PartialMessage:
        client = await self._session.client()
        return client.get_partial_messageable(card.channel_id).get_partial_message(card.id)
