"""A cog to manage new minecraft versions"""

from typing import TYPE_CHECKING, Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog, Context, hybrid_group
from discord.ext.commands.bot import app_commands

from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import PagedList, reply_payload, text_layout
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import requires
from squid.core.i18n import _
from squid.permissions.domain.catalogue import VERSION_ENTRY_CREATE
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

VERSIONS_PER_PAGE = 50
"""Versions per page, which is a few years of releases and still a readable run of tokens."""


class VersionTracker[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="VersionTracker"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.version_service = bot.services.versions

    @hybrid_group(name="version")
    async def version_group(self, ctx: Context[BotT]) -> None:
        """List and manage recognized Minecraft versions."""
        await ctx.send_help("version")

    @version_group.command(name="list")
    async def versions(self, ctx: Context[BotT]):
        """List the Minecraft versions the bot recognizes."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        versions_human_readable = await self.version_service.list_display("Java")
        paginator = PagedList(
            t(locale, _("Recognized Java versions")),
            versions_human_readable,
            empty=t(locale, _("No Java versions are recognized yet.")),
            locale=locale,
            # A version is one short token, so a page is a comma-separated run of them rather
            # than fifty paragraphs; the list used to stop at 20 with a TODO in its place.
            page_size=VERSIONS_PER_PAGE,
            separator=", ",
        )
        await paginator.send(ctx)

    @autocompletes(version_string="approved_source_versions")
    @version_group.command(name="add")
    @requires(VERSION_ENTRY_CREATE)
    @app_commands.rename(version_string="version")
    async def add_version(self, ctx: commands.Context, edition: Literal["Java", "Bedrock"], version_string: str):
        """Add a Minecraft version to the database."""
        version = await self.version_service.add(version_string, edition=edition)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_payload(
            ctx,
            text_layout(t(locale, _("Version added successfully: {version}"), version=version)),
        )

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message):
        """Parse messages in the version-tracking channel and add them to the database"""
        minecraft_version_tracker_channel = self.bot.community_config.version_tracker_channel_id

        channel_id = message.channel.id
        if channel_id != minecraft_version_tracker_channel:
            return

        first_line = message.content.split("\n", 1)[0]
        version = await self.version_service.add(first_line)
        locale = await resolve_locale(message, self.bot.services.settings)
        await send_to(self.bot.get_channel(channel_id))(  # type: ignore
            text_layout(t(locale, _("Version added successfully: {version}"), version=version))
        )


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VersionTracker(bot))
