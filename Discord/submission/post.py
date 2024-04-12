"""Helper functions for posting submissions to discord channels."""
import discord

import Discord.settings as settings
import Database.message as msg  # FIXME: horrible name
from Database.enums import Status
from Discord.config import SETTABLE_CHANNELS_TYPE
from Database.builds import Build


# TODO: make this inside a cog and remove the client parameter?
def get_channel_type_to_post_to(build: Build) -> SETTABLE_CHANNELS_TYPE:
    """Gets the type of channel to post a submission to."""
    status = build.submission_status
    if status == Status.PENDING:
        return "Vote"
    elif status == Status.DENIED:
        raise ValueError("Denied submissions should not be posted.")

    if build.record_category is None:
        return "Builds"
    else:
        return build.record_category  # type: ignore


async def get_channels_to_post_to(client: discord.Client, build: Build) -> list[discord.TextChannel]:
    """Gets all channels which a submission should be posted to."""
    # Get channel type to post submission to
    channel_type = get_channel_type_to_post_to(build)
    # TODO: Special handling for "Vote" channel type, it should only be posted to OWNER_SERVER
    if channel_type is None:
        return []

    channels = []
    # For each server the bot can see
    for guild in client.guilds:
        # Find the channel (if set) that is set for this post to go to
        channel = await settings.get_channel_for(guild, channel_type)
        if channel is None:
            continue
        else:
            channels.append(channel)

    return channels


async def send_submission(client: discord.Client, build: Build):
    """Posts a submission to the appropriate channels in every server the bot is in."""
    # TODO: There are no checks to see if the submission has already been posted, or if the submission is actually a record
    channels = await get_channels_to_post_to(client, build)
    em = build.generate_embed()

    for channel in channels:
        message = await channel.send(embed=em)
        await msg.add_message(channel.guild.id, build.id, message.channel.id, message.id)


async def send_submission_to_server(client: discord.Client, build: Build, server_id: int) -> None:
    """Posts a submission to the appropriate channel in a specific server."""
    channels = await get_channels_to_post_to(client, build)
    em = build.generate_embed()

    for channel in channels:
        if channel.guild.id == server_id:
            message = await channel.send(embed=em)
            await msg.add_message(channel.guild.id, build.id, message.channel.id, message.id)


# TODO: merge server, channel_id, message_id, build_id into a single object
async def edit_post(client: discord.Client, server: discord.Guild, channel_id: int, message_id: int, build_id: int) -> None:
    """Updates a post according to the information given by the build_id."""
    # TODO: Check whether the message_id corresponds to the build_id
    build = await Build.from_id(build_id)
    em = build.generate_embed()
    channel = client.get_channel(channel_id)
    message = await channel.fetch_message(message_id)

    updated_message = await message.edit(embed=em)
    await msg.update_message(updated_message.id)


async def update_build_posts(client: discord.Client, build: Build) -> None:
    """Updates all posts for a build."""
    # Get all messages for a build
    messages = await msg.get_build_messages(build.id)
    em = build.generate_embed()

    for message in messages:
        channel = client.get_channel(message['channel_id'])
        message = await channel.fetch_message(message['message_id'])
        await message.edit(embed=em)
        await msg.update_message(message.id)
