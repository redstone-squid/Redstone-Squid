"""Discord REST adapters for vote member facts and vote card reactions, for processes without a gateway.

Both adapters send their requests through one `DiscordRestSession`. discord.py tracks rate limits per
`HTTPClient`, so sharing a single client keeps every request made with the bot token in one rate-limit
accounting instead of letting each adapter discover the limits separately.
"""

import asyncio
import contextlib
import enum
import logging
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Any, Protocol, override

import aiohttp
from discord.errors import DiscordException, Forbidden, NotFound
from discord.http import HTTPClient, Route
from discord.partial_emoji import PartialEmoji

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


class DiscordRestClient(Protocol):
    """The discord.py `HTTPClient` operations these adapters use."""

    # These return awaitables rather than being declared `async`: discord.py's `HTTPClient`
    # methods are plain methods handing back the coroutine `request` produced, which satisfies
    # this structurally but not the stricter `CoroutineType` an `async def` in a protocol asks for.
    def get_member(self, guild_id: int, member_id: int) -> Awaitable[Any]: ...

    def get_message(self, channel_id: int, message_id: int) -> Awaitable[Any]: ...

    def get_reaction_users(
        self, channel_id: int, message_id: int, emoji: str, limit: int, after: int | None = None
    ) -> Awaitable[Any]: ...

    def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> Awaitable[Any]: ...

    def remove_reaction(self, channel_id: int, message_id: int, emoji: str, member_id: int) -> Awaitable[Any]: ...

    async def close(self) -> None: ...


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


class DiscordRestSession:
    """A bot-token HTTP client that logs in on first use and is shared by the REST adapters.

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
        self._http: DiscordRestClient | None = None
        self._owns_http = True
        self._closed = False
        self._login_lock = asyncio.Lock()

    @classmethod
    def wrapping(cls, http: DiscordRestClient) -> DiscordRestSession:
        """Use an already logged-in client, which `aclose` will not close."""
        session = cls("")
        session._http = http
        session._owns_http = False
        return session

    async def client(self) -> DiscordRestClient:
        """Return the logged-in client, logging in on the first call."""
        if self._closed:
            msg = "The Discord REST session is shut down."
            raise DiscordRestSessionClosedError(msg)
        if self._http is not None:
            return self._http
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

    async def aclose(self) -> None:
        """Close the client this session created, once."""
        self._closed = True
        http, self._http = self._http, None
        if self._owns_http and http is not None:
            await http.close()


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
            http = await self._session.client()
            payload = await http.get_member(guild_id, discord_id)
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


_REACTION_PAGE_SIZE = 100
"""The most users Discord returns for one reaction per request."""


def _route_emoji(emoji: str) -> str:
    """Write a stored emoji the way reaction URLs need it: Unicode as is, custom as `name:id`."""
    parsed = PartialEmoji.from_str(emoji)
    return parsed.name if parsed.id is None else f"{parsed.name}:{parsed.id}"


class DiscordRestVoteCards:
    """Read and change the reactions on vote cards through Discord's REST API.

    This implements `VoteCardReactions` for the worker, which has no gateway connection and
    so no message cache: every read fetches the card and pages through each option's users.

    A card that is deleted or hidden reads as None. Other Discord failures, such as a rate
    limit longer than `MAX_RATE_LIMIT_WAIT_SECONDS` or an outage, propagate.
    """

    def __init__(self, session: DiscordRestSession) -> None:
        self._session = session

    async def read(self, card: VoteMessage, emojis: Sequence[str]) -> CardReactions | None:
        http = await self._session.client()
        try:
            message = await http.get_message(card.channel_id, card.id)
        except NotFound, Forbidden:
            return None
        # The message lists each reaction once with whether this bot made it; only the
        # emojis someone reacted with need their users paged through.
        mine = {
            str(PartialEmoji.from_dict(reaction["emoji"])): bool(reaction.get("me"))
            for reaction in message.get("reactions", ())
        }
        voters = {emoji: await self._human_users(http, card, emoji) for emoji in emojis if emoji in mine}
        return CardReactions(voters, frozenset(emoji for emoji in emojis if mine.get(emoji)))

    async def remove(self, card: VoteMessage, emoji: str, user_id: int) -> None:
        http = await self._session.client()
        with contextlib.suppress(NotFound, Forbidden):
            await http.remove_reaction(card.channel_id, card.id, _route_emoji(emoji), user_id)

    async def add_own(self, card: VoteMessage, emoji: str) -> None:
        http = await self._session.client()
        with contextlib.suppress(NotFound, Forbidden):
            await http.add_reaction(card.channel_id, card.id, _route_emoji(emoji))

    async def _human_users(self, http: DiscordRestClient, card: VoteMessage, emoji: str) -> list[int]:
        """Page through everyone reacting with `emoji`, leaving out bots."""
        user_ids: list[int] = []
        after: int | None = None
        while True:
            page = await http.get_reaction_users(
                card.channel_id, card.id, _route_emoji(emoji), _REACTION_PAGE_SIZE, after=after
            )
            user_ids.extend(int(user["id"]) for user in page if not user.get("bot"))
            if len(page) < _REACTION_PAGE_SIZE:
                return user_ids
            after = int(page[-1]["id"])
