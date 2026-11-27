"""Cache voter membership and resolve account/role voting capabilities."""

import enum
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from time import monotonic
from typing import Protocol

import aiohttp
import discord
from discord.errors import DiscordException, Forbidden, NotFound

from squid.core.actors import UserActor
from squid.core.permissions.application.ports import ActorPermissionChecker
from squid.core.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.core.voting.domain import VoteKind
from squid.core.voting.errors import DiscordMemberServiceUnavailableError
from squid.discord_rest import DiscordRestClosedError

logger = logging.getLogger(__name__)


class VoterDiscordIdLookup(Protocol):
    """The account-to-snowflake read this adapter needs to reach Discord at all."""

    async def discord_id_for(self, account_id: int) -> int | None: ...


class _NotAMember(enum.Enum):
    """Why a member lookup produced no actor."""

    ABSENT = enum.auto()
    """Discord says this user is not in the guild."""
    UNKNOWN = enum.auto()
    """The bot cannot read the guild, so membership is unproven either way."""


type _Membership = UserActor | _NotAMember


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

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> UserActor | None:
        """Return `actor`'s answer; every kind's nodes resolve together, so the kind changes nothing."""
        del kind
        return await self.actor(account_id, guild_id)

    async def actor(self, account_id: int, guild_id: int) -> UserActor | None:
        """Return cached or fetched membership facts for an interactive request.

        Missing identities, absent members, and unreadable guilds return None. Other
        lookup failures raise `DiscordMemberServiceUnavailableError`.
        """
        discord_id = await self._discord_id(account_id)
        if discord_id is None:
            return None
        membership = await self._membership(account_id, discord_id, guild_id)
        return membership if isinstance(membership, UserActor) else None

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> UserActor | None:
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
                return UserActor(account_id=account_id, guild_id=guild_id)
            membership = await self._membership(account_id, discord_id, guild_id)
        except Exception:
            logger.warning(
                "Could not refresh Discord membership facts for vote session",
                exc_info=True,
                extra={"squid.discord.guild_id": guild_id},
            )
            return None
        match membership:
            case UserActor():
                return membership
            case _NotAMember.ABSENT:
                return UserActor(account_id=account_id, discord_user_id=discord_id, guild_id=guild_id)
            case _NotAMember.UNKNOWN:
                return None

    async def resolve_many(
        self, account_ids: Sequence[int], guild_id: int, kind: VoteKind
    ) -> dict[int, UserActor | None]:
        """Use the REST membership cache; Discord's REST API has no bulk lookup."""
        return {account_id: await self.resolve(account_id, guild_id, kind) for account_id in dict.fromkeys(account_ids)}

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
            membership = cached[1]
        else:
            membership = await self._fetch_membership(account_id, discord_id, guild_id)
            self._cache[cache_key] = (now + self._cache_ttl_seconds, membership)
        if not isinstance(membership, UserActor):
            return membership
        # Cache Discord facts, not account ownership or permission decisions. A
        # merge can move an identity, and permission epochs can change mid-TTL.
        actor = replace(membership, account_id=account_id)
        if self._permissions is not None:
            capabilities = (
                await self._permissions.check(
                    actor, VOTE_LOG_DELETE_CAST.name, VOTE_WEIGHT_STAFF.name, VOTE_POLL_CLOSE_ANY.name
                )
            ).allowed_nodes
            actor = replace(actor, capabilities=capabilities)
        return actor

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

    async def _actor_from_payload(self, payload: object, account_id: int, discord_id: int, guild_id: int) -> UserActor:
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
        return UserActor(
            account_id=account_id, discord_user_id=discord_id, guild_id=guild_id, discord_role_ids=role_ids
        )

    @staticmethod
    def _malformed_member(guild_id: int) -> DiscordMemberServiceUnavailableError:
        msg = "Discord returned malformed guild membership information."
        return DiscordMemberServiceUnavailableError(msg, context={"guild_id": guild_id})
