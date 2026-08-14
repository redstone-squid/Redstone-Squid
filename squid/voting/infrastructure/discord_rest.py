"""Discord REST adapter for resolving current guild-member vote facts."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic

import httpx

from squid.permissions.application.ports import ActorCapabilityResolver
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.voting.domain import VoteActor, VoteKindLiteral
from squid.voting.errors import DiscordMemberServiceUnavailableError

logger = logging.getLogger(__name__)
DEFAULT_DISCORD_API_URL = "https://discord.com/api/v10"
type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


class DiscordRestActorResolver:
    """Resolve vote actors through Discord's guild-member REST endpoint."""

    def __init__(
        self,
        bot_token: str,
        *,
        capabilities: ActorCapabilityResolver | None = None,
        client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = 300,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = monotonic,
        api_url: str = DEFAULT_DISCORD_API_URL,
    ) -> None:
        self._token = bot_token
        self._capabilities = capabilities
        self._client = client or httpx.AsyncClient(timeout=10)
        self._owns_client = client is None
        self._cache_ttl_seconds = cache_ttl_seconds
        self._sleep = sleep
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, VoteActor | None]] = {}
        self._api_url = api_url.rstrip("/")

    async def member(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKindLiteral) -> VoteActor | None:
        """Return current member facts, raising when Discord cannot answer reliably."""
        del kind  # Every kind's nodes resolve together, so one load answers all of them.
        cache_key = (guild_id, discord_id)
        cached = self._cache.get(cache_key)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._request_member(discord_id, guild_id)
        if response.status_code in {403, 404}:
            actor = None
        elif response.status_code == 200:
            actor = await self._actor_from_response(response, account_id, discord_id, guild_id)
        else:
            raise self._unavailable(response.status_code)
        self._cache[cache_key] = (now + self._cache_ttl_seconds, actor)
        return actor

    async def resolve(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKindLiteral) -> VoteActor | None:
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
        """Close the internally-owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def _request_member(self, discord_id: int, guild_id: int) -> httpx.Response:
        url = f"{self._api_url}/guilds/{guild_id}/members/{discord_id}"
        try:
            response = await self._client.get(url, headers={"Authorization": f"Bot {self._token}"})
            if response.status_code == 429:
                retry_after = self._retry_after(response)
                await self._sleep(retry_after)
                response = await self._client.get(url, headers={"Authorization": f"Bot {self._token}"})
        except DiscordMemberServiceUnavailableError:
            raise
        except httpx.HTTPError as exc:
            msg = "Discord member lookup failed."
            raise DiscordMemberServiceUnavailableError(
                msg,
                context={"guild_id": guild_id, "error_type": type(exc).__name__},
            ) from exc
        if response.status_code == 429:
            raise self._unavailable(response.status_code)
        return response

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            value = float(response.json()["retry_after"])
        except (KeyError, TypeError, ValueError) as exc:
            msg = "Discord returned a malformed rate-limit response."
            raise DiscordMemberServiceUnavailableError(
                msg,
                context={"status": response.status_code},
            ) from exc
        if not 0 <= value <= 60:
            msg = "Discord requested an unsupported rate-limit delay."
            raise DiscordMemberServiceUnavailableError(
                msg,
                context={"status": response.status_code, "retry_after": value},
            )
        return value

    async def _actor_from_response(
        self, response: httpx.Response, account_id: int, discord_id: int, guild_id: int
    ) -> VoteActor:
        """Build an actor from the member payload, capabilities included.

        This used to hardcode both capability flags to False, so a `delete_log`
        vote cast over REST was always rejected as ineligible. The node is
        grantable to a Discord role, so the role ids already in this payload
        answer it -- no extra guild-permission fetch, and the Manage-Server
        bridge, which is the one source not represented here, is the
        lowest-priority one anyway.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise DiscordRestActorResolver._malformed_member(response, guild_id) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
            raise DiscordRestActorResolver._malformed_member(response, guild_id)
        try:
            role_ids = frozenset(int(role_id) for role_id in payload["roles"])
        except (TypeError, ValueError) as exc:
            raise DiscordRestActorResolver._malformed_member(response, guild_id) from exc
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
    def _malformed_member(response: httpx.Response, guild_id: int) -> DiscordMemberServiceUnavailableError:
        msg = "Discord returned malformed guild membership information."
        return DiscordMemberServiceUnavailableError(
            msg,
            context={"guild_id": guild_id, "status": response.status_code},
        )

    @staticmethod
    def _unavailable(status: int) -> DiscordMemberServiceUnavailableError:
        return DiscordMemberServiceUnavailableError(
            "Discord member lookup was unsuccessful.",
            context={"status": status},
        )
