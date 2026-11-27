"""Keep the roles stored for voters in step with Discord, and read roles live.

Build reviews are weighed from stored roles in the network's owner guild, so a
ballot from the web or another guild's card weighs the same as one from the owner
guild. Member events keep those rows current, and a full pass over the owner
guild's member cache catches events missed while the bot was down. No other guild
is stored: Discord-only sessions use the roles a voter reacts with. Only members
with an account are stored.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

import discord

from squid.bot.actors import has_manage_server
from squid.core.actors import ActingContext
from squid.core.voting.domain import GuildMember

if TYPE_CHECKING:
    import squid.bot.app


def guild_member(member: discord.Member) -> GuildMember:
    """Describe a Discord member the way voting stores it."""
    return GuildMember(member.id, frozenset(role.id for role in member.roles), has_manage_server(member))


class VoterMemberSync:
    """Write the owner guild's member changes into stored voter memberships."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self._votes = bot.services.votes
        self._owner_guild_id = bot.owner_server_id

    async def record(self, members: Iterable[discord.Member]) -> None:
        """Store the owner guild's members among `members`; others are ignored."""
        rows = [guild_member(member) for member in members if self._stores(member.guild) and not member.bot]
        if rows and self._owner_guild_id is not None:
            await self._votes.record_members(self._owner_guild_id, rows, context=ActingContext.current())

    async def reconcile(self, guild: discord.Guild) -> None:
        """Replace the owner guild's stored members with its member cache.

        Skipped while the cache is incomplete, since a partial list would forget
        members who are only missing from the cache.
        """
        if not self._stores(guild) or not guild.chunked:
            return
        members = [guild_member(member) for member in guild.members if not member.bot]
        await self._votes.record_members(guild.id, members, complete=True, context=ActingContext.current())

    async def forget(self, guild_id: int, user_id: int) -> None:
        if guild_id == self._owner_guild_id:
            await self._votes.forget_member(guild_id, user_id, context=ActingContext.current())

    def _stores(self, guild: discord.Guild) -> bool:
        return guild.id == self._owner_guild_id


class ReactingMembers:
    """Read a reacting user's roles from the gateway, for recounting Discord-only sessions."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self._bot = bot

    async def member(self, guild_id: int, discord_user_id: int) -> GuildMember | None:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return None
        member = (await self._bot.members.resolve(guild, (discord_user_id,))).get(discord_user_id)
        return None if member is None else guild_member(member)
