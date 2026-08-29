"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from typing import TYPE_CHECKING, cast, override

import discord
from beartype.door import is_bearable
from discord import app_commands
from discord.ext.commands import Cog, Context, guild_only, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.errors import ErrorHandledModal
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import edit_layout, error_layout, info_layout, no_mentions
from squid.bot.utils.permissions import requires
from squid.core.i18n import SUPPORTED_LOCALES, _
from squid.permissions.domain.catalogue import (
    SETTINGS_SERVER_EDIT,
    SETTINGS_SERVER_VIEW,
    SETTINGS_VOTING_EDIT,
)
from squid.settings.domain import ScalarChannelSetting, Setting
from squid.voting.domain import RoleWeight, VoteChoice, VoteKind, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Settings"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.settings_service = bot.services.settings

    @hybrid_group(name="settings")
    @requires(SETTINGS_SERVER_VIEW, SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT, mode="any")
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
    @requires(SETTINGS_SERVER_VIEW)
    async def show_server_settings(self, ctx: Context[BotT]):
        """Show all settings for this server."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            settings = await self.settings_service.get_all(ctx.guild.id)
            desc = ""
            for setting, value in settings.items():
                if is_bearable(setting, ScalarChannelSetting):  # pyright: ignore[reportArgumentType]
                    value = cast(int | None, value)
                    if value is None:
                        desc += t(locale, _("{setting} channel: _Not set_\n"), setting=setting)
                        continue
                    # noinspection PyTypeHints: PyCharm thinks this cast is invalid
                    channel = cast(GuildMessageable | None, ctx.guild.get_channel(value))
                    display_value = channel.name if channel is not None else t(locale, _("_Not found_"))
                    desc += t(locale, _("{setting} channel: {value}\n"), setting=setting, value=display_value)
                else:  # Should not happen, but may happen if the schema is updated and this code is not
                    desc += t(locale, _("{setting}: {value}\n"), setting=setting, value=value)

            await edit_layout(
                sent_message,
                info_layout(title=t(locale, _("Current Settings")), description=desc),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="get")
    @app_commands.rename(setting="type")
    @requires(SETTINGS_SERVER_VIEW)
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
                case _:  # pyright: ignore[reportUnnecessaryComparison]  # Should not happen, but may happen if the schema is updated and this code is not
                    title = setting
                    description = str(await self.settings_service.get(ctx.guild.id, setting))

            await edit_layout(
                sent_message,
                info_layout(title=title, description=description),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(channel=app_commands.locale_str(_("The channel to send this type of message to")))
    @app_commands.rename(setting="type")
    @requires(SETTINGS_SERVER_EDIT)
    async def change_setting(
        self,
        ctx: Context[BotT],
        setting: Setting,
        channel: GuildMessageable | None = None,
    ):
        """Change the server's setting."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            if is_bearable(setting, ScalarChannelSetting):  # pyright: ignore[reportArgumentType]
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
            else:  # Should not happen, but may happen if the schema is updated and this code is not
                await edit_layout(
                    sent_message,
                    error_layout(t(locale, _("Error")), t(locale, _("This setting is not supported."))),
                    allowed_mentions=no_mentions(),
                )
                raise AssertionError()

    @settings_hybrid_group.command(name="clear")
    @app_commands.rename(setting="type")
    @requires(SETTINGS_SERVER_EDIT)
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
    @requires(SETTINGS_SERVER_EDIT)
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

    @settings_hybrid_group.group(name="voting")
    @requires(SETTINGS_SERVER_VIEW, SETTINGS_VOTING_EDIT, mode="any")
    async def voting_settings(self, ctx: Context[BotT]) -> None:
        """Configure vote emojis and role multipliers."""
        await ctx.send_help("settings voting")

    @voting_settings.command(name="show")
    @requires(SETTINGS_SERVER_VIEW)
    async def show_voting(self, ctx: Context[BotT], kind: VoteKind = VoteKind.BUILD) -> None:
        """Show effective voting configuration for a session kind."""
        assert ctx.guild is not None
        preset = await self.bot.services.votes.emoji_preset(ctx.guild.id, kind)
        weights = await self.bot.services.votes.get_role_weights(ctx.guild.id, kind)
        aliases = "\n".join(f"{option.choice.value}: {option.emoji}" for option in preset.options)
        roles = "\n".join(f"<@&{weight.role_id}>: {weight.multiplier:g}x" for weight in weights) or "None"
        await ctx.send(f"**{kind.value} emojis**\n{aliases}\n\n**Role multipliers**\n{roles}", ephemeral=True)

    @voting_settings.command(name="emojis")
    @requires(SETTINGS_VOTING_EDIT)
    async def edit_voting_emojis(self, ctx: Context[BotT], kind: VoteKind) -> None:
        """Open the emoji preset editor."""
        assert ctx.guild is not None
        if ctx.interaction is None:
            await ctx.send("Use this as a slash command to open the emoji editor.")
            return
        preset = await self.bot.services.votes.emoji_preset(ctx.guild.id, kind)
        value = "\n".join(f"{option.choice.value} | {option.emoji}" for option in preset.options)
        await ctx.interaction.response.send_modal(  # pyrefly: ignore[no-matching-overload]
            VoteEmojiModal(self, kind, value)
        )

    @voting_settings.command(name="weight-set")
    @requires(SETTINGS_VOTING_EDIT)
    async def set_vote_weight(self, ctx: Context[BotT], kind: VoteKind, role: discord.Role, multiplier: float) -> None:
        """Set one role multiplier for a session kind."""
        assert ctx.guild is not None
        if role.guild != ctx.guild:
            await ctx.send("That role is not from this server.", ephemeral=True)
            return
        try:
            weight = RoleWeight(ctx.guild.id, kind, role.id, multiplier)
        except InvalidVoteConfigurationError as error:
            await ctx.send(str(error), ephemeral=True)
            return
        await self.bot.services.votes.set_role_weight(weight)
        await ctx.send("Voting role weight updated.", ephemeral=True)

    @voting_settings.command(name="weight-remove")
    @requires(SETTINGS_VOTING_EDIT)
    async def remove_vote_weight(self, ctx: Context[BotT], kind: VoteKind, role: discord.Role) -> None:
        """Remove one role multiplier for a session kind."""
        assert ctx.guild is not None
        if role.guild != ctx.guild:
            await ctx.send("That role is not from this server.", ephemeral=True)
            return
        await self.bot.services.votes.remove_role_weight(ctx.guild.id, kind, role.id)
        await ctx.send("Voting role weight removed.", ephemeral=True)

    @voting_settings.command(name="reset")
    @requires(SETTINGS_VOTING_EDIT)
    async def reset_voting(self, ctx: Context[BotT], kind: VoteKind | None = None) -> None:
        """Reset voting configuration for one kind or the whole server."""
        assert ctx.guild is not None
        await self.bot.services.votes.reset_configuration(ctx.guild.id, kind)
        await ctx.send("Voting configuration reset.", ephemeral=True)


class VoteEmojiModal(ErrorHandledModal):
    """Edit an ordered guild emoji preset as one choice/emoji pair per line."""

    def __init__(self, cog: SettingsCog, kind: VoteKind, value: str):
        super().__init__(title=f"{kind.value} vote emojis")
        self.cog = cog
        self.kind = kind
        self.aliases = discord.ui.TextInput(
            default=value,
            style=discord.TextStyle.paragraph,
            placeholder="approve | 👍\ndeny | 👎",
            min_length=1,
            max_length=1000,
        )
        self.add_item(discord.ui.Label(text="One `choice | emoji` per line", component=self.aliases))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This editor requires a server.", ephemeral=True)
            return
        options: list[VoteOption] = []
        try:
            for position, line in enumerate(filter(None, (line.strip() for line in self.aliases.value.splitlines()))):
                parts = [part.strip() for part in line.split("|", 1)]
                if len(parts) != 2:
                    msg = "Each line must use `choice | emoji`."
                    raise InvalidVoteConfigurationError(msg)  # noqa: TRY301
                choice_text, emoji = parts
                choice = VoteChoice.GENERIC if self.kind is VoteKind.GENERIC else VoteChoice(choice_text)
                parsed = discord.PartialEmoji.from_str(emoji)
                if parsed.is_custom_emoji():
                    custom = interaction.guild.get_emoji(parsed.id or 0)
                    if custom is None or not custom.is_usable():
                        msg = f"The custom emoji {emoji} is inaccessible."
                        raise InvalidVoteConfigurationError(msg)  # noqa: TRY301
                options.append(
                    VoteOption(
                        emoji,
                        choice,
                        identifier=str(position + 1) if self.kind == "generic" else choice.value,
                        guild_id=interaction.guild.id,
                        label=f"Option {position + 1}" if self.kind == "generic" else None,
                        position=position,
                    )
                )
            await self.cog.bot.services.votes.set_emoji_preset(interaction.guild.id, self.kind, options)
        except (InvalidVoteConfigurationError, ValueError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message("Voting emojis updated for new sessions.", ephemeral=True)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
