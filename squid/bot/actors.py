"""Resolve Discord identities and membership into application actors."""

from typing import TYPE_CHECKING, Any

import discord
from discord.ext.commands import Context
from whenever import Instant

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

_ACTOR_ATTRIBUTE = "_squid_actor"
"""Memo shared by a prefix command and its parent-group permission checks."""


class AccountIdCache:
    """Cache Discord-to-account lookups without creating accounts.

    Known accounts live for `ttl_seconds`; missing accounts use the shorter
    `miss_ttl_seconds` so new links become visible sooner. Reaching `max_entries`
    clears the whole cache rather than evicting one entry. `forget` invalidates
    a known identity change immediately.
    """

    def __init__(self, *, ttl_seconds: float = 300, miss_ttl_seconds: float = 30, max_entries: int = 4096) -> None:
        self._entries: dict[int, tuple[int | None, Instant]] = {}
        self._ttl_seconds = ttl_seconds
        self._miss_ttl_seconds = miss_ttl_seconds
        self._max_entries = max_entries

    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        """The account id behind a Discord id, or None when there is no account."""
        now = Instant.now()
        cached = self._entries.get(discord_id)
        if cached is not None and cached[1] > now:
            return cached[0]

        account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(discord_id))
        account_id = account.id if account is not None else None
        ttl = self._ttl_seconds if account_id is not None else self._miss_ttl_seconds
        if len(self._entries) >= self._max_entries:
            self._entries.clear()
        self._entries[discord_id] = (account_id, now.add(seconds=ttl))
        return account_id

    def forget(self, discord_id: int) -> None:
        """Drop one entry, for when an account has just been linked."""
        self._entries.pop(discord_id, None)


def has_manage_server(member: discord.Member) -> bool:
    """Return whether the member owns the guild or has Administrator or Manage Server."""
    permissions = member.guild_permissions
    return member.id == member.guild.owner_id or permissions.administrator or permissions.manage_guild


class DiscordActorResolver:
    """Build actors using one bot's account cache and application-owner rules."""

    def __init__(self, bot: RedstoneSquid) -> None:
        self._bot = bot

    async def resolve(
        self,
        source: sd.Request[Any] | Context[Any] | discord.Member,
        *,
        refresh: bool = False,
    ) -> Actor:
        """Resolve a request, command context, or already-fetched member.

        Normal checks use the supplied membership and cached account lookup.
        Command contexts also memoize the actor across their parent-group checks.
        With `refresh=True`, fetch current guild membership and bypass both
        caches. Discord failures propagate; a departed member is denied.
        """
        if isinstance(source, discord.Member):
            user, guild = source, source.guild
            memoize = False
        elif isinstance(source, sd.Request):
            user, guild = source.user, source.guild
            memoize = False
        else:
            user, guild = source.author, source.guild
            memoize = not refresh
            if memoize and isinstance(memoized := getattr(source, _ACTOR_ATTRIBUTE, None), Actor):
                return memoized

        member = user if isinstance(user, discord.Member) else None
        if refresh:
            member = None
            if guild is not None:
                try:
                    member = await guild.fetch_member(user.id)
                except discord.NotFound as error:
                    raise AuthorizationError from error
            account = await self._bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
            account_id = None if account is None else account.id
        else:
            account_id = await self._bot.account_ids.resolve(self._bot.services.accounts, user.id)

        actor = Actor(
            discord_user_id=user.id,
            account_id=account_id,
            discord_role_ids=frozenset(role.id for role in member.roles) if member is not None else frozenset(),
            guild_id=None if guild is None else guild.id,
            is_bot_owner=await self._bot.is_owner(user),
            discord_guild_admin=member is not None and has_manage_server(member),
        )
        if memoize:
            setattr(source, _ACTOR_ATTRIBUTE, actor)
        return actor
