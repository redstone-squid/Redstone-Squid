"""Read an account's guild roles over Discord's REST API, for explaining its permissions."""

import enum
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import Protocol

import aiohttp
import discord
from discord.errors import DiscordException, Forbidden, NotFound

from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import UserActor
from squid.core.permissions.errors import DiscordMemberServiceUnavailableError
from squid.discord_rest import DiscordRestClosedError


class DiscordIdLookup(Protocol):
    """The account-to-snowflake read this adapter needs to reach Discord at all."""

    async def discord_id_for(self, account_id: int) -> int | None: ...


class AccountDiscordIds:
    """Find an account's Discord user ID through the account application service."""

    def __init__(self, accounts: AccountService) -> None:
        self._accounts = accounts

    async def discord_id_for(self, account_id: int) -> int | None:
        account = await self._accounts.get_account_by_id(account_id)
        if account is None:
            return None
        identity = next(iter(account.identities_for(IdentityProvider.DISCORD)), None)
        return None if identity is None else identity.discord_id


class _NotAMember(enum.Enum):
    """Why a member lookup produced no actor."""

    ABSENT = enum.auto()
    """Discord says this user is not in the guild."""
    UNKNOWN = enum.auto()
    """The bot cannot read the guild, so membership is unproven either way."""


type _Membership = UserActor | _NotAMember


class DiscordRestGuildMembers:
    """Look up an account's roles in a guild over Discord's REST API.

    The account supplies a Discord identity; a member lookup supplies its guild
    roles. Lookups are cached per guild and Discord user for `cache_ttl_seconds`,
    including absent members and unreadable guilds.
    """

    def __init__(
        self,
        client: discord.Client,
        *,
        discord_ids: DiscordIdLookup | None = None,
        cache_ttl_seconds: float = 300,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._client = client
        self._discord_ids = discord_ids
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[int, int], tuple[float, _Membership]] = {}

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
        # Cache Discord facts, not account ownership: a merge can move an identity.
        return replace(membership, account_id=account_id)

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
        """Validate the member payload's roles.

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
