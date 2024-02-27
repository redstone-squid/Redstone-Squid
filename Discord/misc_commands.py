import discord
import discord.ext.commands as commands
from discord.ext.commands import command, Context, Cog

import Discord.utils as utils
from Discord.config import SOURCE_CODE_URL, BOT_NAME, FORM_LINK


class Miscellaneous(Cog):
    def __init__(self, bot):
        self.bot = bot

    @command()
    async def invite_link(self, ctx: Context):
        """Invite me to your other servers!"""
        await ctx.send(
            f'https://discordapp.com/oauth2/authorize?client_id={str(ctx.bot.user.id)}&scope=bot&permissions=8')

    # Docstring can't be an f-string, so we use the help parameter instead.
    @command(help=f"Link to {BOT_NAME}'s source code.")
    async def source_code(self, ctx: Context):
        await ctx.send(f'Source code can be found at: {SOURCE_CODE_URL}.')

    @command()
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

    @command(name="red", hidden=True)
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
