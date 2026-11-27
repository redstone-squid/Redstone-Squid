"""Keep the roles stored for voters in step with Discord.

Ballots are weighed from stored memberships rather than live lookups, so every
frontend weighs them alike. Member events keep single rows current, and a full
pass over each guild's member cache catches events missed while the bot was down.
Only members with a consented account are stored.
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
    """Write member changes the gateway reports into stored voter memberships."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self._votes = bot.services.votes

    async def record(self, members: Iterable[discord.Member]) -> None:
        """Store the given members, which may come from several guilds."""
        by_guild: dict[int, list[GuildMember]] = {}
        for member in members:
            if not member.bot:
                by_guild.setdefault(member.guild.id, []).append(guild_member(member))
        for guild_id, rows in by_guild.items():
            await self._votes.record_members(guild_id, rows, context=ActingContext.current())

    async def reconcile(self, guild: discord.Guild) -> None:
        """Replace the guild's stored members with its member cache.

        Skipped while the cache is incomplete, since a partial list would forget
        members who are only missing from the cache.
        """
        if not guild.chunked:
            return
        members = [guild_member(member) for member in guild.members if not member.bot]
        await self._votes.record_members(guild.id, members, complete=True, context=ActingContext.current())

    async def forget(self, guild_id: int, user_id: int) -> None:
        await self._votes.forget_member(guild_id, user_id, context=ActingContext.current())
