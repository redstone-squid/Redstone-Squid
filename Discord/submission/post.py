"""Helper functions for posting submissions to discord channels."""
import discord

import Discord.utils as utils
import Discord.settings as settings
import Database.message as msg
from Database.submission import Submission


def generate_submission_embed(submission_obj):
    # Title -------------------------------------------------------------------------------
    # Category
    title = submission_obj.get_title()

    # Description -------------------------------------------------------------------------
    description = submission_obj.get_description()

    # Embed -------------------------------------------------------------------------------
    if description is None:
        em = discord.Embed(title=title, colour=utils.discord_green)
    else:
        em = discord.Embed(title=title, description=description, colour=utils.discord_green)

    fields = submission_obj.get_meta_fields()
    for key, val in fields.items():
        em.add_field(name=key, value=val, inline=True)

    if submission_obj.image_url:
        em.set_image(url=submission_obj.image_url)

    em.set_footer(text=f'Submission ID: {submission_obj.id}.')

    return em


# Get the channels ['Smallest', 'Fastest', 'Smallest Observerless', 'fFastest Observerless', 'First'] to post record to
def get_channel_type_to_post_to(submission_obj: Submission) -> str | None:
    if submission_obj.base_category == 'First':
        return 'First'

    if submission_obj.base_category == 'Fastest' or submission_obj.base_category == 'Fastest Smallest':
        if submission_obj.so_restrictions is None:
            return 'Fastest'
        elif 'Observerless' in submission_obj.so_restrictions:
            return 'Fastest Observerless'
        else:
            return 'Fastest'
    elif submission_obj.base_category == 'Smallest' or submission_obj.base_category == 'Smallest Fastest':
        if submission_obj.so_restrictions is None:
            return 'Smallest'
        elif 'Observerless' in submission_obj.so_restrictions:
            return 'Smallest Observerless'
        else:
            return 'Smallest'

    return None


# Gets all channels which a submission should be posted to
def get_channels_to_post_to(client: discord.Client, submission_obj: Submission) -> list[discord.TextChannel]:
    # Get channel type to post submission to
    channel_type = get_channel_type_to_post_to(submission_obj)
    channels = []

    # For each server the bot can see
    # FIXME: bug here, bot may not be configured in a server, and then it fails
    for guild in client.guilds:
        # Find the channel (if set) that is set for this post to go to
        channel = settings.get_record_channel_for(guild, channel_type)

        if not channel:
            continue

        channels.append(channel)

    return channels


# Posts submission to each server in the channel the server settings worksheet
async def post_submission(client: discord.Client, submission_obj: Submission):
    channels = get_channels_to_post_to(client, submission_obj)
    em = generate_submission_embed(submission_obj)

    for channel in channels:
        message = await channel.send(embed=em)
        msg.update_message(channel.guild.id, submission_obj.id, message.channel.id, message.id)


async def post_submission_to_server(client: discord.Client, submission_obj: Submission, server_id: int):
    channels = get_channels_to_post_to(client, submission_obj)
    em = generate_submission_embed(submission_obj)

    for channel in channels:
        if channel.guild.id == server_id:
            message = await channel.send(embed=em)
            msg.update_message(channel.guild.id, submission_obj.id, message.channel.id, message.id)


# Updates post to conform to the submission obj
async def edit_post(client: discord.Client, server: discord.Guild, channel_id: int, message_id: str, submission_obj: Submission):
    em = generate_submission_embed(submission_obj)
    channel = client.get_channel(channel_id)
    message = await channel.fetch_message(int(message_id))

    updated_message = await message.edit(embed=em)
    msg.update_message(server.id, submission_obj.id, channel_id, updated_message.id)
