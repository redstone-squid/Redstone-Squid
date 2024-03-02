import time
from typing import Literal

import discord
from discord import InteractionResponse
from discord.ext.commands import Context, has_any_role, hybrid_group, Cog, GroupCog, command, HybridGroup, \
    hybrid_command

import Discord.utils as utils
import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions
import Database.message as msg
import Database.main as DB

submission_roles = ['Admin', 'Moderator', 'Redstoner']
# submission_roles = ["Everyone"]

class Submissions(Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_group(name='submissions', invoke_without_command=True)
    async def submission_hybrid_group(self, ctx: Context):
        """View, confirm and deny submissions."""
        await ctx.send_help('submissions')

    @submission_hybrid_group.command(name='open')
    @has_any_role(*submission_roles)
    async def open_function(self, ctx: Context):
        """Shows an overview of all submissions open for review."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        # Updating open worksheet to contain all submitted from google form.
        submissions.open_form_submissions()

        # Creating list of submissions
        open_submissions = submissions.get_open_submissions()

        desc = None
        if len(open_submissions) == 0:
            desc = 'No open submissions.'
        else:
            desc = []
            for sub in open_submissions:
                # ID - Title
                # by Creators - submitted by Submitter
                desc.append(f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators))}_ - _submitted by {sub.submitted_by}_")
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Open Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.edit(embed=em)

    @submission_hybrid_group.command(name='view')
    @has_any_role(*submission_roles)
    async def view_function(self, ctx: Context, index: int):
        """Displays an open submission."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        open_submissions = submissions.get_open_submissions()

        result = None
        for sub in open_submissions:
            if sub.id == index:
                result = sub
                break

        await sent_message.delete()
        if result is None:
            return await ctx.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return await ctx.send(embed=post.generate_submission_embed(result))

    # confirm_function
    @submission_hybrid_group.command(name='confirm')
    @has_any_role(*submission_roles)
    async def confirm_function(self, ctx: Context, index: int):
        """Marks a submission as confirmed."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        sub = submissions.get_open_submission(index)

        if sub is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        await post.post_submission(self.bot, sub)
        submissions.confirm_submission(sub.id)

        return await sent_message.edit(embed=utils.info_embed('Success', 'Submission has successfully been confirmed.'))

    @submission_hybrid_group.command(name='deny')
    @has_any_role(*submission_roles)
    async def deny_function(self, ctx: Context, index: int):
        """Marks a submission as denied."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        sub = submissions.get_open_submission(index)

        if sub is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        submissions.deny_submission(sub.id)

        return await sent_message.edit(embed=utils.info_embed('Success', 'Submission has successfully been denied.'))

    @submission_hybrid_group.command(name='outdated')
    @has_any_role(*submission_roles)
    async def outdated_function(self, ctx: Context):
        """Shows an overview of all discord posts that require updating."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        # Creating list of submissions
        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        desc = None
        if len(outdated_submissions) == 0:
            desc = 'No outdated submissions.'
        else:
            desc = []
            for sub in outdated_submissions:
                sub_obj = sub[1]
                desc.append(f"**{sub_obj.id}** - {sub_obj.get_title()}\n_by {', '.join(sorted(sub_obj.creators))}_ - _submitted by {sub_obj.submitted_by}_")
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)

        # Sending embed
        return await sent_message.edit(embed=em)

    @submission_hybrid_group.command(name='update')
    @has_any_role(*submission_roles)
    async def update_function(self, ctx, index: int):
        """Updated an outdated discord post."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        submission_id = index
        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        sub = None
        for outdated_sub in outdated_submissions:
            if outdated_sub[1].id == submission_id:
                sub = outdated_sub
                break

        if sub is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No outdated submissions with that ID.'))

        if sub[0] is None:
            await post.post_submission_to_server(self.bot, sub[1], ctx.guild.id)
        else:
            await post.edit_post(self.bot, ctx.guild, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

        return await sent_message.edit(embed=utils.info_embed('Success', 'Post has successfully been updated.'))

    @submission_hybrid_group.command(name='update_all')
    @has_any_role(*submission_roles)
    async def update_all_function(self, ctx):
        """Updates all outdated discord posts."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        for sub in outdated_submissions:
            if sub[0] is None:
                await post.post_submission_to_server(self.bot, sub[1], ctx.guild.id)
            else:
                await post.edit_post(self.bot, ctx.guild, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

        return await sent_message.edit(embed=utils.info_embed('Success', 'All posts have been successfully updated.'))

    @hybrid_command(name='submit')
    async def submit(self, interaction: discord.Interaction, record_category: Literal['Smallest', 'Fastest', 'First'],
                     door_width: int, door_height: int, pattern: str, door_type: str, width_of_build: int,
                     height_of_build: int, depth_of_build: int,
                     works_in: str,
                     first_order_restrictions: str = '',
                     second_order_restrictions: str = '', information_about_build: str = '',
                     relative_closing_time: int = -1,
                     relative_opening_time: int = -1, date_of_creation: str = '', in_game_name_of_creator: str = '',
                     locationality: str = '', directionality: str = '',
                     link_to_image: str = '', link_to_youtube_video: str = '',
                     link_to_world_download: str = '', server_ip: str = '', coordinates: str = '',
                     command_to_get_to_build: str = '', your_ign_or_discord: str = ''):
        """Submits a record to the database directly."""
        # FIXME: Discord WILL pass integers even if we specify a string. Need to convert them to strings.

        # noinspection PyTypeChecker
        response: InteractionResponse = interaction.response
        await response.defer()

        # TODO: Discord only allows 25 options. For now, ignore the absolute times.
        absolute_closing_time = ''
        absolute_opening_time = ''

        if relative_closing_time == -1:
            relative_closing_time = ''
        if relative_opening_time == -1:
            relative_opening_time = ''

        # noinspection PyTypeChecker
        followup: discord.Webhook = interaction.followup
        message: discord.WebhookMessage | None = await followup.send('Received')
        timestamp = time.strftime('%d/%m/%Y %H:%M:%S')
        form_wks = DB.get_form_submissions_worksheet()
        submissions.add_submission_raw({
            'Record Category': record_category,
            'Door Width': door_width,
            'Door Height': door_height,
            'Pattern': pattern,
            'Door Type': door_type,
            'First Order Restrictions': first_order_restrictions,
            'Second Order Restrictions': second_order_restrictions,
            'Information About Build': information_about_build,
            'Width of Build': width_of_build,
            'Height of Build': height_of_build,
            'Depth of Build': depth_of_build,
            'Relative Closing Time': relative_closing_time,
            'Relative Opening Time': relative_opening_time,
            'Absolute Closing Time': absolute_closing_time,
            'Absolute Opening Time': absolute_opening_time,
            'Date of Creation': date_of_creation,
            'Timestamp': timestamp,
            'In-Game Name of Creator': in_game_name_of_creator,
            'Locationality': locationality,
            'Directionality': directionality,
            'Works In': works_in,
            'Link to Image': link_to_image,
            'Link to YouTube Video': link_to_youtube_video,
            'Link to World Download': link_to_world_download,
            'Server IP': server_ip,
            'Coordinates': coordinates,
            'Command to Get to Build': command_to_get_to_build,
            'Your IGN or Discord': your_ign_or_discord
        })
        await message.edit(content='Record submitted successfully!')
