"""Keep stored guild and channel state in step with Discord.

The database remembers which guilds the bot is in (`server_settings.in_server`) and which
channels settings and starboards point at. Discord announces changes to both through gateway
events, and this cog applies them as they arrive. Events are lost while the bot is offline, so
each one also has a repair that compares the database with discord.py's gateway cache:

- On a fresh gateway session (`on_ready`, not a resume), the full guild list replaces the stored
  presence flags.
- When a guild becomes available (at startup, after an outage, or on joining), its cached
  channels are compared with the channels stored for it.

These repairs read only what the gateway already delivered, so a restart does not cause a burst
of Discord requests. The one exception is a stored channel that is missing from the cache: a
thread that has been archived is also missing, so the channel is fetched once to tell the two
apart. That costs one request per stale reference, and the reference is cleared when it is gone.
"""

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class GuildStateCog[BotT: squid.bot.app.RedstoneSquid](commands.Cog):
    """Apply guild and channel lifecycle events, and repair the ones that were missed."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.settings = bot.services.settings
        self.starboards = bot.services.starboards

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Make stored guild presence match the guilds in this gateway session."""
        joined, left = await self.settings.sync_presence(guild.id for guild in self.bot.guilds)
        if joined or left:
            logger.info(
                "Repaired guild presence missed while disconnected",
                extra={"squid.guild.joined": len(joined), "squid.guild.left": len(left)},
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        # Settings survive leaving a guild, so a rejoined guild may point at channels deleted since.
        await self.settings.guild_joined(guild.id)
        await self.reconcile_channels(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.settings.guild_removed(guild.id)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self.reconcile_channels(guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.forget_channels(channel.guild.id, {channel.id})

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        # Settings may name a thread as a destination; on_guild_channel_delete never covers threads.
        await self.forget_channels(payload.guild_id, {payload.thread_id})

    async def reconcile_channels(self, guild: discord.Guild) -> None:
        """Forget stored channels of this guild that Discord no longer has.

        An unavailable guild has no channels in the cache, so it is skipped until it becomes
        available again.
        """
        if guild.unavailable:
            return
        referenced = await self.settings.referenced_channels(guild.id) | await self.starboards.referenced_channels(
            guild.id
        )
        uncached = [channel_id for channel_id in referenced if guild.get_channel_or_thread(channel_id) is None]
        gone = {channel_id for channel_id in uncached if await self._is_deleted(channel_id)}
        if gone:
            logger.info(
                "Forgetting channels deleted while disconnected",
                extra={"squid.discord.guild_id": guild.id, "squid.guild.channels_gone": len(gone)},
            )
            await self.forget_channels(guild.id, gone)

    async def forget_channels(self, guild_id: int, channel_ids: set[int]) -> None:
        """Clear settings that name the channels and stop starboards using them."""
        await self.settings.forget_channels(guild_id, channel_ids)
        await self.starboards.forget_channels(guild_id, channel_ids)

    async def _is_deleted(self, channel_id: int) -> bool:
        """Ask Discord whether an uncached channel is gone, treating any doubt as present."""
        try:
            await self.bot.fetch_channel(channel_id)
        except discord.NotFound:
            return True
        except discord.HTTPException:
            # Forbidden means it exists but is hidden; anything else is not an answer.
            return False
        return False


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(GuildStateCog(bot))
