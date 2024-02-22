
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import CommandLeaf
from Discord.command_branch import CommandBranch
from Discord.permissions import *

import Discord.settings.settings as settings
import Database.submission as submission
import Database.server_settings as server_settings
import Database.message as msg

def generate_embed(submission_obj):
    # Title -------------------------------------------------------------------------------
    # Catagory
    title = submission_obj.get_title()

    # Description -------------------------------------------------------------------------
    description = submission_obj.get_description()
    
    # Embed -------------------------------------------------------------------------------
    em = None
    if description is None:
        em = discord.Embed(title = title,  colour = utils.discord_green)
    else:
        em = discord.Embed(title = title, description = description, colour = utils.discord_green)

    fields = submission_obj.get_meta_fields()
    for key, val in fields.items():
        em.add_field(name = key, value = val, inline = True)
    
    if submission_obj.image_url:
        em.set_image(url = submission_obj.image_url)

    em.set_footer(text = 'Submission ID: {}.'.format(submission_obj.id))

    return em

# Get the channels ['smallest', 'fastest', 'smallest_observerless', 'fastest_observerless'] to post record to
def get_channel_type_to_post_to(submission_obj):
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
def get_channels_to_post_to(client, submission_obj):
    # Get channel type to post submission to
    channel_type = get_channel_type_to_post_to(submission_obj)
    channels = []

    # For each server the bot can see
    # TODO: bug here, bot may not be configured in a server, and then it fails
    for guild in client.guilds:
        # Find the channel (if set) that is set for this post to go to
        channel = settings.get_channel_for(guild, channel_type)

        if not channel:
            continue
        
        channels.append(channel)
    
    return channels

# Posts submission to each server in the channel the server settings worksheet
async def post_submission(client, submission_obj):
    channels = get_channels_to_post_to(client, submission_obj)
    em = generate_embed(submission_obj)

    for channel in channels:
        message = await client.send_message(channel, embed = em)
        msg.update_message(channel.server.id, submission_obj.id, message.channel.id, message.id)

async def post_submission_to_server(client, submission_obj, server_id):
    channels = get_channels_to_post_to(client, submission_obj)
    em = generate_embed(submission_obj)

    for channel in channels:
        if channel.server.id == server_id:
            message = await client.send_message(channel, embed = em)
            msg.update_message(channel.server.id, submission_obj.id, message.channel.id, message.id)

# Updates post to conform to the submission obj
async def edit_post(client, server, channel_id, message_id, submission_obj):

    em = generate_embed(submission_obj)
    channel = client.get_channel(str(channel_id))
    message = await client.get_message(channel, int(message_id))

    updated_message = await client.edit_message(message, embed = em)
    msg.update_message(server.id, submission_obj.id, str(channel_id), updated_message.id)