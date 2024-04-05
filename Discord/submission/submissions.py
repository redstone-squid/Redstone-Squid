import time
from datetime import datetime
from typing import Literal

import discord
from discord import InteractionResponse
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, has_any_role, hybrid_group, Cog, hybrid_command

import Discord.config
import Discord.utils as utils
import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions
import Database.message as msg
from Discord.submission.submission import Submission

submission_roles = ['Admin', 'Moderator', 'Redstoner']
# TODO: Set up a webhook for the bot to handle google form submissions.

class SubmissionsCog(Cog, name='Submissions'):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_group(name='submissions', invoke_without_command=True)
    async def submission_hybrid_group(self, ctx: Context):
        """View, confirm and deny submissions."""
        await ctx.send_help('submissions')

    @submission_hybrid_group.command(name='pending')
    async def get_pending_submissions(self, ctx: Context):
        """Shows an overview of all submissions pending review."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        pending_submissions = submissions.get_pending_submissions()

        if len(pending_submissions) == 0:
            desc = 'No open submissions.'
        else:
            desc = []
            for sub in pending_submissions:
                # ID - Title
                # by Creators - submitted by Submitter
                desc.append(
                    f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators))}_ - _submitted by {sub.submitted_by}_")
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Open Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.edit(embed=em)

    @submission_hybrid_group.command(name='view')
    async def view_function(self, ctx: Context, submission_id: int):
        """Displays a submission."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        submission = submissions.get_submission(submission_id)

        if submission is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return await sent_message.edit(embed=submission.generate_embed())

    @staticmethod
    def is_owner_server(ctx: Context):
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            # TODO: Make a custom error for this.
            # https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=is_owner#discord.discord.ext.commands.on_command_error
            raise commands.CommandError('This command can only be executed on certain servers.')
        return True

    @submission_hybrid_group.command(name='confirm')
    @has_any_role(*submission_roles)
    async def confirm_function(self, ctx: Context, submission_id: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        submission = submissions.confirm_submission(submission_id)
        if submission is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        await post.send_record(self.bot, submission)

        return await sent_message.edit(embed=utils.info_embed('Success', 'Submission has successfully been confirmed.'))

    @submission_hybrid_group.command(name='deny')
    @has_any_role(*submission_roles)
    async def deny_function(self, ctx: Context, submission_id: int):
        """Marks a submission as denied."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        sub = submissions.deny_submission(submission_id)

        if sub is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No open submission with that ID.'))

        return await sent_message.edit(embed=utils.info_embed('Success', 'Submission has successfully been denied.'))

    @submission_hybrid_group.command(name='outdated')
    async def outdated_function(self, ctx: Context):
        """Shows an overview of all discord posts that require updating."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        # Creating list of submissions
        outdated_messages = msg.get_outdated_messages(ctx.guild.id)

        if len(outdated_messages) == 0:
            desc = 'No outdated submissions.'
            em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)
            return await sent_message.edit(embed=em)

        subs = submissions.get_submissions([message['submission_id'] for message in outdated_messages])

        # TODO: Consider using get_unsent_messages too, and then merge the two lists, with different headers.
        # unsent_submissions = submissions.get_unsent_submissions(ctx.guild.id)

        desc = []
        for sub in subs:
            desc.append(
                f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators))}_ - _submitted by {sub.submitted_by}_")
        desc = '\n\n'.join(desc)

        em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)
        return await sent_message.edit(embed=em)

    @submission_hybrid_group.command(name='update')
    @has_any_role(*submission_roles)
    async def update_function(self, ctx, submission_id: int):
        """Update or post an outdated discord post to this server."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        message = msg.get_outdated_message(ctx.guild.id, submission_id)
        if message is None:
            return await sent_message.edit(embed=utils.error_embed('Error', 'No outdated submissions with that ID.'))

        # If message isn't yet tracked, add it.
        # await post.send_submission_to_server(self.bot, message[1], ctx.guild.id)

        await post.edit_post(self.bot, ctx.guild, message['channel_id'], message['message_id'],
                             message['submission_id'])
        return await sent_message.edit(embed=utils.info_embed('Success', 'Post has successfully been updated.'))

    @submission_hybrid_group.command(name='update_all')
    @has_any_role(*submission_roles)
    async def update_all_function(self, ctx):
        """Updates all outdated discord posts in this server."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        outdated_messages = msg.get_outdated_messages(ctx.guild.id)
        for message in outdated_messages:
            # If message isn't yet tracked, add it.
            # await post.send_submission_to_server(self.bot, sub, ctx.guild.id)
            await post.edit_post(self.bot, ctx.guild, message['channel_id'], message['message_id'],
                                 message['submission_id'])

        return await sent_message.edit(embed=utils.info_embed('Success', 'All posts have been successfully updated.'))

    @hybrid_command(name='versions')
    async def versions(self, ctx: Context):
        """Shows a list of versions the bot recognizes."""
        await ctx.send(config.VERSIONS_LIST)

    @app_commands.command(name='submit')
    @app_commands.describe(
        record_category='The category of the record. If none, use "None".',
        door_width='The width of the door itself. Like 2x2 piston door.',
        door_height='The height of the door itself. Like 2x2 piston door.',
        pattern='The pattern type of the door. For example, "full lamp" or "funnel".',
        door_type='Door, Skydoor, or Trapdoor.',
        build_width='The width of the build.',
        build_height='The height of the build.',
        build_depth='The depth of the build.',
        works_in='The versions the build works in. Default to newest version. /versions for full list.',
        wiring_placement_restrictions='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.',
        component_restrictions='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.',
        information_about_build='Any additional information about the build.',
        normal_closing_time='The time it takes to close the door, in gameticks. (1s = 20gt)',
        normal_opening_time='The time it takes to open the door, in gameticks. (1s = 20gt)',
        date_of_creation='The date the build was created.',
        in_game_name_of_creator='The in-game name of the creator(s).',
        locationality='Whether the build works everywhere, or only in certain locations.',
        directionality='Whether the build works in all directions, or only in certain directions.',
        link_to_image='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"',
        link_to_youtube_video='A link to a video of the build.',
        link_to_world_download='A link to download the world.',
        server_ip='The IP of the server where the build is located.',
        coordinates='The coordinates of the build in the server.',
        command_to_get_to_build='The command to get to the build in the server.'
    )
    async def submit(self, interaction: discord.Interaction, record_category: Literal['Smallest', 'Fastest', 'First', 'None'],
                     door_width: int, door_height: int, pattern: str, door_type: Literal['Door', 'Skydoor', 'Trapdoor'],
                     build_width: int, build_height: int, build_depth: int,
                     # Optional parameters
                     works_in: str = Discord.config.VERSIONS_LIST[-1],
                     wiring_placement_restrictions: str = None,
                     component_restrictions: str = None, information_about_build: str = None,
                     normal_opening_time: int = None, normal_closing_time: int = None,
                     date_of_creation: str = None, in_game_name_of_creator: str = None,
                     locationality: Literal["Locational", "Locational with fixes"] = None,
                     directionality: Literal["Directional", "Directional with fixes"] = None,
                     link_to_image: str = None, link_to_youtube_video: str = None,
                     link_to_world_download: str = None, server_ip: str = None, coordinates: str = None,
                     command_to_get_to_build: str = None):
        """Submits a record to the database directly."""
        # FIXME: Discord WILL pass integers even if we specify a string. Need to convert them to strings.

        # noinspection PyTypeChecker
        response: InteractionResponse = interaction.response
        await response.defer()

        # noinspection PyTypeChecker
        followup: discord.Webhook = interaction.followup
        message: discord.WebhookMessage | None = \
            await followup.send(embed=utils.info_embed('Working', 'Updating information...'))

        submission_id = submissions.add_submission_raw({
            'record_category': record_category if record_category != 'None' else None,
            'submission_status': Submission.PENDING,
            'door_width': door_width,
            'door_height': door_height,
            'pattern': pattern,
            'door_type': door_type,
            'wiring_placement_restrictions': wiring_placement_restrictions,
            'component_restrictions': component_restrictions,
            'information': information_about_build,
            'build_width': build_width,
            'build_height': build_height,
            'build_depth': build_depth,
            'normal_closing_time': normal_closing_time,
            'normal_opening_time': normal_opening_time,
            'visible_closing_time': None,  # TODO: Discord only allows 25 options. For now, ignore the absolute times.
            'visible_opening_time': None,
            'date_of_creation': date_of_creation,
            'submission_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'creators_ign': in_game_name_of_creator,
            'locationality': locationality,
            'directionality': directionality,
            'functional_versions': works_in,
            'image_link': link_to_image,
            'video_link': link_to_youtube_video,
            'world_download_link': link_to_world_download,
            'server_ip': server_ip,
            'coordinates': coordinates,
            'command_to_build': command_to_get_to_build,
            'submitted_by': str(interaction.user)
        })
        # TODO: preview the submission
        await message.edit(embed=utils.info_embed('Success', f'Build submitted successfully!\nThe submission ID is: {submission_id}'))

    @app_commands.command(name='edit')
    @app_commands.describe(
        door_width='The width of the door itself. Like 2x2 piston door.',
        door_height='The height of the door itself. Like 2x2 piston door.',
        pattern='The pattern type of the door. For example, "full lamp" or "funnel".',
        door_type='Door, Skydoor, or Trapdoor.',
        build_width='The width of the build.',
        build_height='The height of the build.',
        build_depth='The depth of the build.',
        works_in='The versions the build works in. Default to newest version. /versions for full list.',
        wiring_placement_restrictions='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.',
        component_restrictions='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.',
        information_about_build='Any additional information about the build.',
        normal_closing_time='The time it takes to close the door, in gameticks. (1s = 20gt)',
        normal_opening_time='The time it takes to open the door, in gameticks. (1s = 20gt)',
        date_of_creation='The date the build was created.',
        in_game_name_of_creator='The in-game name of the creator(s).',
        locationality='Whether the build works everywhere, or only in certain locations.',
        directionality='Whether the build works in all directions, or only in certain directions.',
        link_to_image='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"',
        link_to_youtube_video='A link to a video of the build.',
        link_to_world_download='A link to download the world.',
        server_ip='The IP of the server where the build is located.',
        coordinates='The coordinates of the build in the server.',
        command_to_get_to_build='The command to get to the build in the server.'
    )
    async def edit(self, interaction: discord.Interaction, submission_id: int, door_width: int = None, door_height: int = None,
                   pattern: str = None, door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = None, build_width: int = None,
                   build_height: int = None, build_depth: int = None, works_in: str = None, wiring_placement_restrictions: str = None,
                   component_restrictions: str = None, information_about_build: str = None,
                   normal_closing_time: int = None,
                   normal_opening_time: int = None, date_of_creation: str = None, in_game_name_of_creator: str = None,
                   locationality: Literal["Locational", "Locational with fixes"] = None,
                   directionality: Literal["Directional", "Directional with fixes"] = None,
                   link_to_image: str = None, link_to_youtube_video: str = None,
                   link_to_world_download: str = None, server_ip: str = None, coordinates: str = None,
                   command_to_get_to_build: str = None):
        """Edits a record in the database directly."""
        # noinspection PyTypeChecker
        response: InteractionResponse = interaction.response
        await response.defer()

        # noinspection PyTypeChecker
        followup: discord.Webhook = interaction.followup
        message: discord.WebhookMessage | None = \
            await followup.send(embed=utils.info_embed('Working', 'Updating information...'))

        update_values = {
            'last_update': datetime.now().strftime(r'%Y-%m-%d %H:%M:%S.%f'),
            'door_width': door_width,
            'door_height': door_height,
            'pattern': pattern,
            'door_type': door_type,
            'wiring_placement_restrictions': wiring_placement_restrictions,
            'component_restrictions': component_restrictions,
            'information': information_about_build,
            'build_width': build_width,
            'build_height': build_height,
            'build_depth': build_depth,
            'normal_closing_time': normal_closing_time,
            'normal_opening_time': normal_opening_time,
            'date_of_creation': date_of_creation,
            'creators_ign': in_game_name_of_creator,
            'locationality': locationality,
            'directionality': directionality,
            'functional_versions': works_in,
            'image_link': link_to_image,
            'video_link': link_to_youtube_video,
            'world_download_link': link_to_world_download,
            'server_ip': server_ip,
            'coordinates': coordinates,
            'command_to_build': command_to_get_to_build,
            'submitted_by': None
        }
        update_values = {k: v for k, v in update_values.items() if v is not None}

        # Show a preview of the changes
        old_submission = submissions.get_submission(submission_id)
        new_submission = Submission.from_dict({**old_submission.to_dict(), **update_values})
        preview_embed = new_submission.generate_embed()
        # await message.edit(embed=utils.info_embed('Waiting', 'User confirming changes...'))
        await followup.send(embed=preview_embed, ephemeral=True)

        # TODO: Implement a way to confirm the changes. Right now, it updates the record immediately.

        submissions.update_submission(submission_id, update_values)
        await message.edit(embed=utils.info_embed('Success', 'Build edited successfully!'))
