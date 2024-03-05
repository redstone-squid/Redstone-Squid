"""Helper functions for posting submissions to discord channels."""
from typing import Literal

import discord

import Discord.settings as settings
import Database.message as msg  # FIXME: horrible name
from Database import submissions
from Discord.submission.submission import Submission


def get_channel_type_to_post_to(submission: Submission) -> Literal['Smallest', 'Fastest', 'First', 'Builds']:
    """Gets the type of channel to post a submission to."""
    if submission.base_category is None:
        return "Builds"
    else:
        return submission.base_category


def get_channels_to_post_to(client: discord.Client, submission: Submission) -> list[discord.TextChannel]:
    """Gets all channels which a submission should be posted to."""
    # Get channel type to post submission to
    channel_type = get_channel_type_to_post_to(submission)
    if channel_type is None:
        return []

    channels = []
    # For each server the bot can see
    for guild in client.guilds:
        # Find the channel (if set) that is set for this post to go to
        channel = settings.get_record_channel_for(guild, channel_type)
        if channel is None:
            continue
        else:
            channels.append(channel)

    return channels


async def send_submission(client: discord.Client, submission: Submission):
    """Posts a submission to the appropriate channels in every server the bot is in."""
    channels = get_channels_to_post_to(client, submission)
    em = submission.generate_embed()

    for channel in channels:
        message = await channel.send(embed=em)
        msg.update_message(channel.guild.id, submission.id, message.channel.id, message.id)


async def send_submission_to_server(client: discord.Client, submission: Submission, server_id: int):
    """Posts a submission to the appropriate channel in a specific server."""
    channels = get_channels_to_post_to(client, submission)
    em = submission.generate_embed()

    for channel in channels:
        if channel.guild.id == server_id:
            message = await channel.send(embed=em)
            msg.update_message(channel.guild.id, submission.id, message.channel.id, message.id)


async def edit_post(client: discord.Client, server: discord.Guild, channel_id: int, message_id: int, submission_id: int):
    """Edits a post to conform to the submission object."""
    em = submissions.get_submission(submission_id).generate_embed()
    channel = client.get_channel(channel_id)
    message = await channel.fetch_message(message_id)

    updated_message = await message.edit(embed=em)
    msg.update_message(server.id, submission_id, channel_id, updated_message.id)
