"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from typing import TYPE_CHECKING, Annotated, cast

import discord
from beartype.door import is_bearable
from discord import app_commands
from discord.ext.commands import Cog, Context, Greedy, guild_only, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import edit_layout, error_layout, info_layout, no_mentions
from squid.bot.utils.permissions import check_is_staff
from squid.core.i18n import SUPPORTED_LOCALES, _
from squid.settings.domain import ListRoleSetting, ScalarChannelSetting, Setting

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Settings"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.settings_service = bot.services.settings

    @hybrid_group(name="settings")
    @check_is_staff()
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context[BotT]):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        """Let the db know that the bot has joined a new guild."""
        await self.settings_service.guild_joined(guild.id)

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        """Let the db know that the bot has left a guild."""
        await self.settings_service.guild_removed(guild.id)

    @settings_hybrid_group.command(name="list")
    @check_is_staff()
    async def show_server_settings(self, ctx: Context[BotT]):
        """Show all settings for this server."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            settings = await self.settings_service.get_all(ctx.guild.id)
            desc = ""
            for setting, value in settings.items():
                if is_bearable(setting, ScalarChannelSetting):
                    value = cast(int | None, value)
                    if value is None:
                        desc += t(locale, _("{setting} channel: _Not set_\n"), setting=setting)
                        continue
                    # noinspection PyTypeHints: PyCharm thinks this cast is invalid
                    channel = cast(GuildMessageable | None, ctx.guild.get_channel(value))
                    display_value = channel.name if channel is not None else t(locale, _("_Not found_"))
                    desc += t(locale, _("{setting} channel: {value}\n"), setting=setting, value=display_value)
                elif is_bearable(setting, ListRoleSetting):
                    value = cast(list[int], value)
                    roles = [role for role in ctx.guild.roles if role.id in value]
                    display_value = ", ".join(role.name for role in roles) or t(locale, _("_Not set_"))
                    desc += t(locale, _("{setting} roles: {value}\n"), setting=setting, value=display_value)
                else:  # Should not happen, but may happen if the schema is updated and this code is not
                    desc += t(locale, _("{setting}: {value}\n"), setting=setting, value=value)

            await edit_layout(
                sent_message,
                info_layout(title=t(locale, _("Current Settings")), description=desc),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="get")
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def get_setting(self, ctx: Context[BotT], setting: Setting):
        """Show the server's current setting."""
        assert ctx.guild is not None

        title: str
        description: str
        locale = await resolve_locale(ctx, self.settings_service)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            match setting:
                case "Smallest" | "Fastest" | "First" | "Builds" | "Vote":
                    title = t(locale, _("{setting} Channel Info"), setting=setting)
                    value = await self.settings_service.get(ctx.guild.id, setting)
                    if value is None:
                        description = t(locale, _("_Not set_"))
                    else:
                        channel = ctx.guild.get_channel(value)
                        description = (
                            t(locale, _("ID: {id} \n Name: {name}"), id=channel.id, name=channel.name)
                            if channel is not None
                            else t(locale, _("_Not found_"))
                        )
                case "Staff" | "Trusted":
                    title = t(locale, _("{setting} Roles Info"), setting=setting)
                    value = await self.settings_service.get(ctx.guild.id, setting)
                    roles = [role for role in ctx.guild.roles if role.id in value]
                    description = ", ".join(role.name for role in roles) or t(locale, _("_Not set_"))
                case _:  # pyright: ignore[reportUnnecessaryComparison]  # Should not happen, but may happen if the schema is updated and this code is not
                    title = setting
                    description = str(await self.settings_service.get(ctx.guild.id, setting))

            await edit_layout(
                sent_message,
                info_layout(title=title, description=description),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(
        channel=app_commands.locale_str(_("The channel to send this type of message to")),
        roles=app_commands.locale_str(_("The roles which will have this permission")),
    )
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def change_setting(
        self,
        ctx: Context[BotT],
        setting: Setting,
        channel: GuildMessageable | None = None,
        roles: Annotated[list[discord.Role] | None, Greedy[discord.Role]] = None,
    ):
        """Change the server's setting."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        if channel is not None and roles is not None:
            await ctx.send(
                view=error_layout(
                    t(locale, _("Error")),
                    t(locale, _("You can only provide a channel or a list of roles, not both.")),
                ),
                allowed_mentions=no_mentions(),
            )
            return

        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            if is_bearable(setting, ScalarChannelSetting):
                if channel is None:
                    await edit_layout(
                        sent_message,
                        error_layout(
                            t(locale, _("Error")),
                            t(locale, _("You must provide a channel for this setting.")),
                        ),
                        allowed_mentions=no_mentions(),
                    )
                    return

                if ctx.guild.get_channel(channel.id) is None:
                    await edit_layout(
                        sent_message,
                        error_layout(t(locale, _("Error")), t(locale, _("Could not find that channel."))),
                        allowed_mentions=no_mentions(),
                    )
                    return

                # TODO: Add a check when adding channels to the database to make sure they are GuildMessageable
                await self.settings_service.set_channel(ctx.guild.id, setting, channel.id)
                await edit_layout(
                    sent_message,
                    info_layout(
                        t(locale, _("Settings updated")),
                        t(locale, _("{setting} channel has successfully been set."), setting=setting),
                    ),
                    allowed_mentions=no_mentions(),
                )
            elif is_bearable(setting, ListRoleSetting):
                if roles is None:
                    await edit_layout(
                        sent_message,
                        error_layout(
                            t(locale, _("Error")),
                            t(locale, _("You must provide a list of roles for this setting.")),
                        ),
                        allowed_mentions=no_mentions(),
                    )
                    return

                role_ids = [role.id for role in roles]
                if any(role.guild != ctx.guild for role in roles):
                    await edit_layout(
                        sent_message,
                        error_layout(t(locale, _("Error")), t(locale, _("The roles must be from this server."))),
                        allowed_mentions=no_mentions(),
                    )
                    return

                await self.settings_service.set_roles(ctx.guild.id, setting, role_ids)
                await edit_layout(
                    sent_message,
                    info_layout(
                        t(locale, _("Settings updated")),
                        t(locale, _("{setting} roles have successfully been set."), setting=setting),
                    ),
                    allowed_mentions=no_mentions(),
                )
            else:  # Should not happen, but may happen if the schema is updated and this code is not
                await edit_layout(
                    sent_message,
                    error_layout(t(locale, _("Error")), t(locale, _("This setting is not supported."))),
                    allowed_mentions=no_mentions(),
                )
                raise AssertionError()

    @settings_hybrid_group.command(name="clear")
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def clear_setting(self, ctx: Context[BotT], setting: Setting):
        """Set this setting to None."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            await self.settings_service.clear(ctx.guild.id, setting)
            await edit_layout(
                sent_message,
                info_layout(
                    t(locale, _("Setting updated")),
                    t(locale, _("{setting} has been cleared."), setting=setting),
                ),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="locale")
    @app_commands.describe(language=app_commands.locale_str(_("The language the bot should respond in")))
    @app_commands.choices(
        language=[app_commands.Choice(name=tag, value=tag) for tag in sorted(SUPPORTED_LOCALES)],
    )
    @check_is_staff()
    async def set_locale(self, ctx: Context[BotT], language: str):
        """Set the language the bot responds with in this server."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        if language not in SUPPORTED_LOCALES:
            await ctx.send(
                view=error_layout(t(locale, _("Error")), t(locale, _("That language is not supported."))),
                allowed_mentions=no_mentions(),
            )
            return

        await self.settings_service.set_locale(ctx.guild.id, language)
        async with self.bot.get_running_message(ctx, locale=language) as sent_message:
            await edit_layout(
                sent_message,
                info_layout(
                    t(language, _("Settings updated")),
                    t(language, _("This server's language has been set to {language}."), language=language),
                ),
                allowed_mentions=no_mentions(),
            )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
