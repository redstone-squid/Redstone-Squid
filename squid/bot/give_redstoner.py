# type: ignore
"""Magical stuff, don't worry about it."""

import asyncio
import re
from typing import TYPE_CHECKING, Any, Self, override

import discord
from discord import Interaction
from discord.ext.commands import Cog, Context, hybrid_command
from discord.ui import Item

from squid.bot._types import GuildMessageable
from squid.bot.utils import check_is_owner_server
from squid.bot.utils.permissions import check_is_staff

if TYPE_CHECKING:
    import squid.bot


class DynamicRemoveOwnRedstonerRoleButton[BotT: "squid.bot.RedstoneSquid", V: discord.ui.View](
    discord.ui.DynamicItem[discord.ui.Button[V]], template=r"remove:role:redstoner"
):
    """A button that allows users to remove their own redstoner role."""

    def __init__(self):
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

        await member.remove_roles(redstoner_role)
        owner = interaction.client.get_user(interaction.client.owner_id)
        assert owner is not None
        redstoner_channel = interaction.client.get_channel(534945678850523138)  # redstoner-corner
        assert isinstance(redstoner_channel, GuildMessageable)
        await redstoner_channel.send(f"{owner.mention}, {member.mention} has removed their own redstoner role.")
        await asyncio.sleep(10)

        await member.add_roles(redstoner_role)
        await interaction.followup.send(f"{member.mention} jk, here is your role back.", ephemeral=True)


class GiveRedstoner[BotT: "squid.bot.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.pattern = re.compile(r"https://discord\.com/channels/\d+/\d+/\d+")

    @Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
        await self.give_redstoner_from_message(message)

    @hybrid_command()
    @check_is_owner_server()
    @check_is_staff()
    async def abc(self, ctx: Context[BotT]):
        view = discord.ui.View()
        view.add_item(DynamicRemoveOwnRedstonerRoleButton())
        await ctx.send("a", view=view)

    @hybrid_command(name="reload_redstoner")
    @check_is_owner_server()
    @check_is_staff()
    async def force_reload_message(self, ctx: Context[BotT], message: discord.Message):
        await self.give_redstoner_from_message(message)

    async def give_redstoner_from_message(self, message: discord.Message) -> None:
        """Give the redstoner role to a user based on a Starboard message."""
        if message.author.id != 700796664276844612:  # @Starboard
            return

        if message.channel.id != 1332630008270684241:  # Thread for listening to starboard messages
            return

        mentions = message.mentions
        if len(mentions) != 1:
            await self.bot.get_channel(1332630008270684241).send(
                f"Expected 1 mention from starboard in {message.jump_url}, got {', '.join(mention.name for mention in mentions)}"
            )
            return

        orig_message_link = self.pattern.search(message.content).group(0)

        redstoner_role = message.guild.get_role(433670432420397060)
        member: discord.Member = mentions[0]
        await member.add_roles(redstoner_role)
        await message.channel.send(
            f"Gave {member.mention} the redstoner role.", allowed_mentions=discord.AllowedMentions.none()
        )

        view = discord.ui.View()
        view.add_item(DynamicRemoveOwnRedstonerRoleButton())
        await self.bot.get_channel(433643026204852224).send(
            f"Hi {member.mention}, you just got the {redstoner_role.mention} role because you received 15 upvotes in {orig_message_link}.",
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False),
            view=view,
        )


async def setup(bot: "squid.bot.RedstoneSquid"):
    bot.add_dynamic_items(DynamicRemoveOwnRedstonerRoleButton)
    await bot.add_cog(GiveRedstoner(bot))
