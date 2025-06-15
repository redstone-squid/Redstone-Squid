# type: ignore
"""Magical stuff, don't worry about it."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, override, Self, Any

import discord
from discord import Interaction
from discord.ext import commands
from discord.ext.commands import Cog
from discord.ui import Item

if TYPE_CHECKING:
    import squid.bot


class DynamicRemoveOwnRedstonerRoleButton[BotT: squid.bot.RedstoneSquid, V: discord.ui.View](
    discord.ui.DynamicItem[discord.ui.Button[V]], template=r"remove:role:redstoner:(\d+)"
):
    """A button that allows users to remove their own redstoner role."""
    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(
            discord.ui.Button(
                label="I'm not a redstoner",
                style=discord.ButtonStyle.red,
                custom_id=f"remove:role:redstoner:{user_id}",
            )
        )

    @classmethod
    @override
    async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]
        cls: type[Self], interaction: Interaction[BotT], item: Item[Any], match: re.Match[str], /
    ) -> Self:
        user_id = int(match.group(1))
        return cls(user_id)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> Any:  # pyright: ignore [reportIncompatibleMethodOverride]
        await interaction.response.defer()
        if interaction.user.id != self.user_id:
            return

        if interaction.guild is None or interaction.guild.id != interaction.client.owner_server_id:
            return

        member = interaction.guild.get_member(self.user_id)
        if member is None:
            return

        redstoner_role = interaction.guild.get_role(433670432420397060)
        if redstoner_role in member.roles:
            await member.remove_roles(redstoner_role)


class GiveRedstoner(Cog):
    def __init__(self, bot: squid.bot.RedstoneSquid):
        self.bot = bot
        self.pattern = re.compile(r"https://discord\.com/channels/\d+/\d+/\d+")

    @Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
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
        view.add_item(DynamicRemoveOwnRedstonerRoleButton(member.id))
        await self.bot.get_channel(433643026204852224).send(
            f"Hi {member.mention}, you just got the {redstoner_role.mention} role because you received 15 upvotes in {orig_message_link}.",
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False), view=view
        )


async def setup(bot: squid.bot.RedstoneSquid):
    bot.add_dynamic_items(DynamicRemoveOwnRedstonerRoleButton)
    await bot.add_cog(GiveRedstoner(bot))
