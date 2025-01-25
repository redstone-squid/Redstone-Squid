# type: ignore
"""Magical stuff, don't worry about it."""

from __future__ import annotations

import re

from typing import TYPE_CHECKING
import discord
from discord.ext.commands import Cog

if TYPE_CHECKING:
    import bot.main


class GiveRedstoner(Cog):
    def __init__(self, bot: bot.main.RedstoneSquid):
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
                f"Expected 1 mention from starboard in {message.jump_url}, got {", ".join(mention.name for mention in mentions)}"
            )
            return

        orig_message_link = self.pattern.search(message.content).group(0)

        redstoner_role = message.guild.get_role(433670432420397060)
        member: discord.Member = mentions[0]
        await member.add_roles(redstoner_role)
        await message.channel.send(f"Gave {member.mention} the redstoner role.", allowed_mentions=discord.AllowedMentions.none())
        await self.bot.get_channel(433643026204852224).send(
            f"Hi {member.mention}, you just got the {redstoner_role.mention} role because you received 5 upvotes in {orig_message_link}.",
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False),
        )


async def setup(bot: bot.main.RedstoneSquid):
    await bot.add_cog(GiveRedstoner(bot))
