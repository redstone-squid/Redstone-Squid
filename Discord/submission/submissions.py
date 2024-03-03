import time
from typing import Literal

import discord
from discord import InteractionResponse
from discord.ext import commands
from discord.ext.commands import Context, has_any_role, hybrid_group, Cog, GroupCog, command, HybridGroup, \
    hybrid_command, guild_only

import Discord.utils as utils
import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions
import Database.message as msg
from Database.database import DatabaseManager
from Database.submission import Submission

submission_roles = ['Admin', 'Moderator', 'Redstoner']
# submission_roles = ["Everyone"]

class SubmissionsCog(Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_group(name='submissions', invoke_without_command=True)
    async def submission_hybrid_group(self, ctx: Context):
        """View, confirm and deny submissions."""
        await ctx.send_help('submissions')

    @submission_hybrid_group.command(name='pending')
    @has_any_role(*submission_roles)
    async def get_pending_submissions(self, ctx: Context):
        """Shows an overview of all submissions pending review."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        db = DatabaseManager()
        pending_submissions_raw = db.table('submissions').select('*').eq('submission_status', Submission.PENDING).execute().data
        pending_submissions = [submissions.Submission.from_dict(sub) for sub in pending_submissions_raw]

        desc = None
        if len(pending_submissions) == 0:
            desc = 'No open submissions.'
        else:
            desc = []
            for sub in pending_submissions:
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
        """Displays a submission."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        db = DatabaseManager()
        submission, count = db.table('submissions').select('*').eq('submission_id', index).execute()
        assert count <= 1

        if submission is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return await sent_message.edit(embed=Submission.from_dict(submission[0]).generate_submission_embed())

    @staticmethod
    def is_owner_server(ctx: Context):
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            # TODO: Make a custom error for this. Then implement
            # https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=is_owner#discord.discord.ext.commands.on_command_error
            raise commands.CommandError('This command can only be executed on certain servers.')
        return True

    @submission_hybrid_group.command(name='confirm')
    @has_any_role(*submission_roles)
    async def confirm_function(self, ctx: Context, index: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        submission = submissions.confirm_submission(index)
        if submission is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        await post.send_submission(self.bot, submission)

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

        sub = submissions.deny_submission(index)

        if sub is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))

        return await sent_message.edit(embed=utils.info_embed('Success', 'Submission has successfully been denied.'))

    # @submission_hybrid_group.command(name='outdated')
    # @has_any_role(*submission_roles)
    # async def outdated_function(self, ctx: Context):
    #     """Shows an overview of all discord posts that require updating."""
    #     # Sending working message.
    #     sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))
    #
    #     # Creating list of submissions
    #     outdated_submissions = msg.get_outdated_messages(ctx.guild.id)
    #
    #     if len(outdated_submissions) == 0:
    #         desc = 'No outdated submissions.'
    #         em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)
    #         return await sent_message.edit(embed=em)
    #
    #     desc = []
    #     for message, sub in outdated_submissions:
    #         desc.append(f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators))}_ - _submitted by {sub.submitted_by}_")
    #     desc = '\n\n'.join(desc)
    #
    #     em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)
    #     return await sent_message.edit(embed=em)
    #
    # @submission_hybrid_group.command(name='update')
    # @has_any_role(*submission_roles)
    # async def update_function(self, ctx, index: int):
    #     """Update or post an outdated discord post to this server."""
    #     # Sending working message.
    #     sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
    #
    #     submission_id = index
    #
    #     sub = msg.get_outdated_message(ctx.guild.id, submission_id)
    #
    #     if sub is None:
    #         return await sent_message.edit(embed=utils.error_embed('Error', 'No outdated submissions with that ID.'))
    #
    #     if sub[0] is None:
    #         # If message isn't yet tracked, add it.
    #         await post.send_submission_to_server(self.bot, sub[1], ctx.guild.id)
    #     else:
    #         await post.edit_post(self.bot, ctx.guild, sub[0]['channel_id'], sub[0]['message_id'], sub[1])
    #
    #     return await sent_message.edit(embed=utils.info_embed('Success', 'Post has successfully been updated.'))
    #
    # @submission_hybrid_group.command(name='update_all')
    # @has_any_role(*submission_roles)
    # async def update_all_function(self, ctx):
    #     """Updates all outdated discord posts in this server."""
    #     sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
    #
    #     outdated_submissions = msg.get_outdated_messages(ctx.guild.id)
    #     for message, sub in outdated_submissions:
    #         if message is None:
    #             await post.send_submission_to_server(self.bot, sub, ctx.guild.id)
    #         else:
    #             await post.edit_post(self.bot, ctx.guild, message['channel_id'], message['message_id'], sub)
    #
    #     return await sent_message.edit(embed=utils.info_embed('Success', 'All posts have been successfully updated.'))

    @hybrid_command(name='submit')
    async def submit(self, interaction: discord.Interaction, record_category: Literal['Smallest', 'Fastest', 'First'],
                     door_width: int, door_height: int, pattern: str, door_type: str, width_of_build: int,
                     height_of_build: int, depth_of_build: int,
                     works_in: str,
                     first_order_restrictions: str = '',
                     second_order_restrictions: str = '', information_about_build: str = '',
                     relative_closing_time: int = 0,
                     relative_opening_time: int = 0, date_of_creation: str = '', in_game_name_of_creator: str = '',
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
        absolute_closing_time = None
        absolute_opening_time = None

        # noinspection PyTypeChecker
        followup: discord.Webhook = interaction.followup
        message: discord.WebhookMessage | None = \
            await followup.send(embed=utils.info_embed('Working', 'Updating information...'))
        submissions.add_submission_raw({
            'record_category': record_category,
            'door_width': door_width,
            'door_height': door_height,
            'pattern': pattern,
            'door_type': door_type,
            'wiring_placement_restrictions': first_order_restrictions,
            'component_restrictions': second_order_restrictions,
            'information': information_about_build,
            'build_width': width_of_build,
            'build_height': height_of_build,
            'build_depth': depth_of_build,
            'relative_closing_time': relative_closing_time,
            'relative_opening_time': relative_opening_time,
            'absolute_closing_time': absolute_closing_time,
            'absolute_opening_time': absolute_opening_time,
            'date_of_creation': date_of_creation,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'creators_ign': in_game_name_of_creator,
            'locationality': locationality,
            'directionality': directionality,
            'functional_versions': works_in,
            'image_link': link_to_image,
            'video_link': link_to_youtube_video,
            'world_download_link': link_to_world_download,
            'server_ip': server_ip,
            'coordinates': coordinates,
            'command_to_get_to_build': command_to_get_to_build,
            'submitter_name': your_ign_or_discord
        })
        await message.edit(embed=utils.info_embed('Success', 'Record submitted successfully!'))
