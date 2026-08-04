# type: ignore
"""Magical stuff, don't worry about it."""

import asyncio
import re
from typing import TYPE_CHECKING, Any, Self, override

import discord
from discord import Interaction
from discord.ext.commands import Cog, Context, hybrid_group
from discord.ui import Item

from squid.bot._types import GuildMessageable
from squid.bot.errors import ErrorHandledLayoutView
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import no_mentions, text_layout
from squid.bot.utils.permissions import check_is_home_server, check_is_server_admin
from squid.community.domain import RedstonerDecisionKind
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


class DynamicRemoveOwnRedstonerRoleButton[
    BotT: "squid.bot.app.RedstoneSquid",
    V: discord.ui.LayoutView,
](discord.ui.DynamicItem[discord.ui.Button[V]], template=r"remove:role:redstoner"):
    """A button that allows users to remove their own redstoner role."""

    def __init__(self):
        # Not translated: this is a persistent button on a shared, public message (not
        # rendered per-viewer), so there is no single "locale" to translate it into.
        super().__init__(
            discord.ui.Button(
                label="I'm not a redstoner",
                style=discord.ButtonStyle.red,
                custom_id="remove:role:redstoner",
            )
        )

    @classmethod
    @override
    async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]
        cls: type[Self], interaction: Interaction[BotT], item: Item[Any], match: re.Match[str], /
    ) -> Self:
        return cls()

    @override
    async def callback(self, interaction: Interaction[BotT]) -> Any:  # pyright: ignore [reportIncompatibleMethodOverride]
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None or interaction.guild.id != interaction.client.owner_server_id:
            return

        member = interaction.user
        redstoner_role = interaction.guild.get_role(433670432420397060)
        if redstoner_role is None or redstoner_role not in member.roles:
            return

        locale = await resolve_locale(interaction, interaction.client.services.settings)
        await member.remove_roles(redstoner_role)
        owner = interaction.client.get_user(interaction.client.owner_id)
        assert owner is not None
        redstoner_channel = interaction.client.get_channel(534945678850523138)  # redstoner-corner
        assert isinstance(redstoner_channel, GuildMessageable)
        await redstoner_channel.send(
            view=text_layout(
                t(
                    locale,
                    _("{owner}, {member} has removed their own redstoner role."),
                    owner=owner.mention,
                    member=member.mention,
                )
            ),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=(owner, member),
                roles=False,
                replied_user=False,
            ),
        )
        await asyncio.sleep(10)

        await member.add_roles(redstoner_role)
        await interaction.followup.send(
            view=text_layout(t(locale, _("{member} — just kidding, here is your role back."), member=member.mention)),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )


class GiveRedstoner[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.service = bot.services.redstoner

    @Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
        await self.give_redstoner_from_message(message)

    @hybrid_group(name="redstoner")
    @check_is_home_server()
    @check_is_server_admin()
    async def redstoner_group(self, ctx: Context[BotT]) -> None:
        """Manage Redstoner role automation."""
        await ctx.send_help("redstoner")

    @redstoner_group.command(name="panel")
    @check_is_home_server()
    @check_is_server_admin()
    async def abc(self, ctx: Context[BotT]):
        """Post the Redstoner role controls."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        view = ErrorHandledLayoutView(timeout=None)
        view.add_item(discord.ui.TextDisplay(t(locale, _("Redstoner role controls"))))
        view.add_item(discord.ui.ActionRow(DynamicRemoveOwnRedstonerRoleButton()))
        await ctx.send(view=view, allowed_mentions=no_mentions())

    @redstoner_group.command(name="resync")
    @check_is_home_server()
    @check_is_server_admin()
    async def force_reload_message(self, ctx: Context[BotT], message: discord.Message):
        """Reprocess a message for Redstoner role automation."""
        await self.give_redstoner_from_message(message)

    async def give_redstoner_from_message(self, message: discord.Message) -> None:
        """Give the redstoner role to a user based on a Starboard message."""
        decision = self.service.evaluate(
            author_id=message.author.id,
            channel_id=message.channel.id,
            mentioned_user_ids=[mention.id for mention in message.mentions],
            content=message.content,
        )
        if decision.kind is RedstonerDecisionKind.IGNORE:
            return

        locale = await resolve_locale(message, self.bot.services.settings)
        if decision.kind is RedstonerDecisionKind.MALFORMED:
            await message.channel.send(
                view=text_layout(t(locale, _("{reason} in {url}"), reason=decision.reason, url=message.jump_url)),
                allowed_mentions=no_mentions(),
            )
            return

        assert decision.member_id is not None
        assert decision.source_message_url is not None
        member = next(mention for mention in message.mentions if mention.id == decision.member_id)
        assert message.guild is not None
        redstoner_role = message.guild.get_role(433670432420397060)
        if redstoner_role is None:
            await message.channel.send(
                view=text_layout(t(locale, _("Could not find the redstoner role."))),
                allowed_mentions=no_mentions(),
            )
            return
        await member.add_roles(redstoner_role)
        await message.channel.send(
            view=text_layout(t(locale, _("Gave {member} the redstoner role."), member=member.mention)),
            allowed_mentions=no_mentions(),
        )

        view = ErrorHandledLayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                t(
                    locale,
                    _("Hi {member}, you received the {role} role after reaching 15 upvotes in {url}."),
                    member=member.mention,
                    role=redstoner_role.mention,
                    url=decision.source_message_url,
                )
            )
        )
        view.add_item(discord.ui.ActionRow(DynamicRemoveOwnRedstonerRoleButton()))
        await self.bot.get_channel(433643026204852224).send(
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False),
            view=view,
        )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    bot.add_dynamic_items(DynamicRemoveOwnRedstonerRoleButton)
    await bot.add_cog(GiveRedstoner(bot))
