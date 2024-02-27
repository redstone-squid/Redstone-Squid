from typing import Optional, Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import command, Context, Cog, Greedy

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

    # @app_commands.command(name='submit')
    # async def submit(self, interaction: discord.Interaction, record_category: Literal['Smallest', 'Fastest'],
    #                  door_width: int, door_height: int, pattern: str, door_type: str, first_order_restrictions: Optional[str],
    #                  second_order_restrictions: Optional[str], information_about_build: Optional[str],
    #                  width_of_build: int, height_of_build: int, depth_of_build: int, relative_closing_time: Optional[int],
    #                  relative_opening_time: Optional[int], absolute_closing_time: Optional[int],
    #                  absolute_opening_time: Optional[int], date_of_creation: str, in_game_name_of_creator: str,
    #                  locationality: Optional[str], directionality: Optional[str],
    #                  versions_which_submission_works_in: str, link_to_image: Optional[str], link_to_youtube_video: Optional[str],
    #                  link_to_world_download: Optional[str], server_ip: Optional[str], coordinates: Optional[str],
    #                  command_to_get_to_build: Optional[str], your_ign_or_discord_handle: Optional[str]):
    #     """Submits a record to the database directly."""
    #     await interaction.response.send_message(f"Received: {text}")

        # formatted as 22/02/2024 12:21:48
        # timestamp = time.strftime('%d/%m/%Y %H:%M:%S')
        # form_wks = DB.get_form_submissions_worksheet()
        # form_wks.append_row([record_category, door_width, door_height, pattern, door_type, first_order_restrictions,
        #                      second_order_restrictions,
        #                      information_about_build, width_of_build, height_of_build, depth_of_build,
        #                      relative_closing_time, relative_opening_time,
        #                      absolute_closing_time, absolute_opening_time, date_of_creation, timestamp,
        #                      in_game_name_of_creator, locationality, directionality,
        #                      versions_which_submission_works_in, link_to_image, link_to_youtube_video,
        #                      link_to_world_download, server_ip, coordinates,
        #                      command_to_get_to_build, your_ign_or_discord_handle])
        # await interaction.response.send_message('Record submitted successfully!')

    @command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync_tree(self, ctx: Context, guilds: Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
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
