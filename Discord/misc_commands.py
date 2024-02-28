from typing import Optional, Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import command, Context, Cog, Greedy, hybrid_command

import Discord.utils as utils
from Discord.config import SOURCE_CODE_URL, BOT_NAME, FORM_LINK


class Miscellaneous(Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def invite_link(self, ctx: Context):
        """Invite me to your other servers!"""
        await ctx.send(
            f'https://discordapp.com/oauth2/authorize?client_id={str(ctx.bot.user.id)}&scope=bot&permissions=8')

    # Docstring can't be an f-string, so we use the help parameter instead.
    @hybrid_command(help=f"Link to {BOT_NAME}'s source code.")
    async def source_code(self, ctx: Context):
        await ctx.send(f'Source code can be found at: {SOURCE_CODE_URL}.')

    @hybrid_command()
    async def submit_record(self, ctx: Context):
        """Links you to our record submission form."""
        em = discord.Embed(title='Submission form.',
                           description=f'You can submit new records with ease via our google form: {FORM_LINK}',
                           colour=utils.discord_green)
        # TODO: image is not showing up.
        # em.set_image(url='https://i.imgur.com/AqYEd1o.png')
        await ctx.send(embed=em)

    @staticmethod
    async def is_my_alt(ctx: Context):
        return ctx.author.id == 1146802450100138004

    @command(name="r", hidden=True)
    @commands.check(is_my_alt)
    async def give_redstoner(self, ctx: Context):
        """Give redstoner role to my alt for testing. Does nothing for others."""
        moderator: discord.Role = ctx.guild.get_role(433670432420397060)
        if moderator in ctx.author.roles:
            await ctx.author.remove_roles(moderator)
        else:
            await ctx.author.add_roles(moderator)

    @command()
    @commands.is_owner()
    async def logs(self, ctx: Context):
        """Sends the Heroku logs link"""
        await ctx.send("https://dashboard.heroku.com/apps/redstone-squid/logs")

    @command(name="s", hidden=True)
    @commands.guild_only()
    @commands.is_owner()
    async def sync(self, ctx: Context, guilds: Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
        """Syncs the slash commands with the discord API."""
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(
                f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
            )
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    @command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context):
        """Sends the database link"""
        await ctx.send("https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221")
