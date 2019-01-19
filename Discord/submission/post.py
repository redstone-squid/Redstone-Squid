
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Discord.settings.settings as settings
import Database.submission as submission
import Database.server_settings as server_settings

def generate_embed(submission_obj):
    # Title -------------------------------------------------------------------------------
    # Catagory
    title = submission_obj.get_title()

    # Description -------------------------------------------------------------------------
    description = submission_obj.get_description()
    
    # Embed -------------------------------------------------------------------------------
    em = discord.Embed(title = title, description = description, colour = utils.discord_green)

    fields = submission_obj.get_meta_fields()
    for key, val in fields.items():
        em.add_field(name = key, value = val, inline = True)
    
    if submission_obj.image_url:
        em.set_image(url = submission_obj.image_url)

    em.set_footer(text = 'Record ID: {}.'.format(submission_obj.id))

    return em

# Get the channels ['smallest', 'fastest', 'smallest_observerless', 'fastest_observerless'] to post record to
def get_channel_type_to_post_to(submission_obj):
    if submission_obj.base_category == 'First':
        return 'First'

    if submission_obj.base_category == 'Fastest' or submission_obj.base_category == 'Fastest Smallest':
        if 'Observerless' in submission_obj.so_restrictions:
            return 'Fastest Observerless'
        else:
            return 'Fastest'
    elif submission_obj.base_category == 'Smallest' or submission_obj.base_category == 'Smallest Fastest':
        if 'Observerless' in submission_obj.so_restrictions:
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
    for server in client.servers:
        # Find the channel (if set) that is set for this post to go to
        channel = settings.get_channel_for(server, channel_type)

        if not channel:
            continue
        
        channels.append(channel)
    
    return channels

# Posts submission to each server in the channel the server settings worksheet
async def post_submission(client, submission_obj):
    channels = get_channels_to_post_to(client, submission_obj)
    em = generate_embed(submission_obj)

    for channel in channels:
        await client.send_message(channel, embed = em)