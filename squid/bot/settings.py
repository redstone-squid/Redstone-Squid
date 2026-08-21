"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, guild_only, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale, t
from squid.bot.settings_view import FOLLOW_DISCORD, SettingsCapabilities, SettingsPanel
from squid.bot.ui import destination
from squid.bot.utils.components import edit_layout, error_layout, info_layout, no_mentions
from squid.bot.utils.mount_registry import SessionKey
from squid.bot.utils.permissions import hide_unless, requires, subject_for
from squid.bot.utils.visibility import personal
from squid.core.i18n import SUPPORTED_LOCALES, _
from squid.permissions.domain.catalogue import (
    SETTINGS_SERVER_EDIT,
    SETTINGS_SERVER_VIEW,
    SETTINGS_VOTING_EDIT,
)
from squid.settings.domain import ScalarChannelSetting
from squid.voting.domain import RoleWeight, VoteKind
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Settings"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.settings_service = bot.services.settings

    @hybrid_group(name="settings", fallback="show")
    @requires(SETTINGS_SERVER_VIEW, SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT, mode="any")
    @hide_unless(manage_guild=True)
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context[BotT]) -> None:
        """Open this server's settings panel."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        view = SettingsPanel(
            settings=self.settings_service,
            votes=self.bot.services.votes,
            guild=ctx.guild,
            author_id=ctx.author.id,
            capabilities=await self._capabilities(ctx),
            locale=locale,
            owner_guild_id=self.bot.owner_server_id,
        )
        # One panel per admin per guild: a second `/settings` replaces the first rather than
        # leaving two live panels writing the same settings service.
        await self.bot.mounts.open(
            view.mount(),
            destination(ctx, visibility="personal", locale=locale),
            key=SessionKey("settings", ctx.author.id, ctx.guild.id),
        )

    async def _capabilities(self, ctx: Context[BotT]) -> SettingsCapabilities:
        """What this caller may do, asked once so the panel can render only that.

        The group admits anyone holding any one of the three nodes, so a caller granted only
        vote configuration reaches the panel with no business seeing the channel pickers.
        """
        subject = await subject_for(ctx)
        permissions = self.bot.services.permissions
        return SettingsCapabilities(
            view_server=await permissions.allows(subject, SETTINGS_SERVER_VIEW),
            edit_server=await permissions.allows(subject, SETTINGS_SERVER_EDIT),
            edit_voting=await permissions.allows(subject, SETTINGS_VOTING_EDIT),
        )

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        """Let the db know that the bot has joined a new guild."""
        await self.settings_service.guild_joined(guild.id)

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        """Let the db know that the bot has left a guild."""
        await self.settings_service.guild_removed(guild.id)

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(
        channel=app_commands.locale_str(_("The channel to use. Leave it out to clear this setting."))
    )
    @app_commands.rename(setting="type")
    @requires(SETTINGS_SERVER_EDIT)
    async def change_setting(
        self,
        ctx: Context[BotT],
        setting: ScalarChannelSetting,
        channel: GuildMessageable | None = None,
    ) -> None:
        """Point one setting at a channel, or clear it. The panel edits several at once."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            if channel is None:
                await self.settings_service.clear(ctx.guild.id, setting)
                await edit_layout(
                    sent_message,
                    info_layout(
                        t(locale, _("Setting updated")),
                        t(locale, _("{setting} has been cleared."), setting=setting),
                    ),
                    allowed_mentions=no_mentions(),
                )
                return

            if ctx.guild.get_channel_or_thread(channel.id) is None:
                await edit_layout(
                    sent_message,
                    error_layout(t(locale, _("Error")), t(locale, _("Could not find that channel."))),
                    allowed_mentions=no_mentions(),
                )
                return

            await self.settings_service.set_channel(ctx.guild.id, setting, channel.id)
            await edit_layout(
                sent_message,
                info_layout(
                    t(locale, _("Settings updated")),
                    t(locale, _("{setting} channel has successfully been set."), setting=setting),
                ),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="locale")
    @app_commands.describe(language=app_commands.locale_str(_("The language the bot should respond in")))
    @app_commands.choices(
        language=[
            app_commands.Choice(name=app_commands.locale_str(_("Follow Discord")), value=FOLLOW_DISCORD),
            *(app_commands.Choice(name=tag, value=tag) for tag in sorted(SUPPORTED_LOCALES)),
        ],
    )
    @requires(SETTINGS_SERVER_EDIT)
    async def set_locale(self, ctx: Context[BotT], language: str) -> None:
        """Set the language the bot responds with in this server."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)

        if language != FOLLOW_DISCORD and language not in SUPPORTED_LOCALES:
            await ctx.send(
                view=error_layout(t(locale, _("Error")), t(locale, _("That language is not supported."))),
                allowed_mentions=no_mentions(),
            )
            return

        if language == FOLLOW_DISCORD:
            await self.settings_service.set_locale(ctx.guild.id, None)
            async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
                await edit_layout(
                    sent_message,
                    info_layout(
                        t(locale, _("Settings updated")),
                        t(locale, _("This server now follows its Discord language.")),
                    ),
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
    @requires(SETTINGS_VOTING_EDIT)
    async def voting_settings(self, ctx: Context[BotT]) -> None:
        """Configure vote emojis and role multipliers."""
        await ctx.send_help("settings voting")

    @voting_settings.command(name="weight-set")
    @requires(SETTINGS_VOTING_EDIT)
    async def set_vote_weight(self, ctx: Context[BotT], kind: VoteKind, role: discord.Role, multiplier: float) -> None:
        """Set one role multiplier for a session kind."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        if role.guild != ctx.guild:
            await self._reply(
                ctx, error_layout(t(locale, _("Error")), t(locale, _("That role is not from this server.")))
            )
            return
        try:
            weight = RoleWeight(ctx.guild.id, kind, role.id, multiplier)
        except InvalidVoteConfigurationError:
            await self._reply(
                ctx,
                error_layout(
                    t(locale, _("Error")),
                    t(locale, _("A vote multiplier must be a positive number, such as 1.5.")),
                ),
            )
            return
        await self.bot.services.votes.set_role_weight(weight)
        await self._reply(
            ctx,
            info_layout(
                t(locale, _("Voting updated")),
                t(locale, _("{role} now counts {multiplier}x."), role=role.name, multiplier=f"{multiplier:g}")
                + self._weight_scope_note(ctx.guild.id, kind, locale),
            ),
        )

    @voting_settings.command(name="weight-remove")
    @requires(SETTINGS_VOTING_EDIT)
    async def remove_vote_weight(self, ctx: Context[BotT], kind: VoteKind, role: discord.Role) -> None:
        """Remove one role multiplier for a session kind."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        if role.guild != ctx.guild:
            await self._reply(
                ctx, error_layout(t(locale, _("Error")), t(locale, _("That role is not from this server.")))
            )
            return
        await self.bot.services.votes.remove_role_weight(ctx.guild.id, kind, role.id)
        await self._reply(
            ctx,
            info_layout(
                t(locale, _("Voting updated")),
                t(locale, _("{role} no longer carries extra weight."), role=role.name)
                + self._weight_scope_note(ctx.guild.id, kind, locale),
            ),
        )

    @voting_settings.command(name="reset")
    @requires(SETTINGS_VOTING_EDIT)
    async def reset_voting(self, ctx: Context[BotT], kind: VoteKind | None = None) -> None:
        """Reset voting configuration for one kind or the whole server."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.settings_service)
        await self.bot.services.votes.reset_configuration(ctx.guild.id, kind)
        await self._reply(
            ctx,
            info_layout(
                t(locale, _("Voting reset")),
                t(locale, _("{kind} voting is back to its defaults."), kind=kind.value)
                if kind is not None
                else t(locale, _("Every kind of voting is back to its defaults.")),
            ),
        )

    async def _reply(self, ctx: Context[BotT], layout: discord.ui.LayoutView) -> None:
        """Answer the caller privately, in the layout system the rest of the bot uses."""
        await ctx.send(view=layout, allowed_mentions=no_mentions(), ephemeral=personal(ctx))

    def _weight_scope_note(self, guild_id: int, kind: VoteKind, locale: str | None) -> str:
        """Warn when this server's multipliers bind nothing it can see."""
        if kind is not VoteKind.BUILD or self.bot.owner_server_id in (None, guild_id):
            return ""
        return t(
            locale,
            _("\n\nBuild reviews are weighted by the network's own server, so this server's multipliers do not apply."),
        )


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
