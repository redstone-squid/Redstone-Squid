
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Database.submission as submission
import Database.server_settings as server_settings

def generate_embed(submission_obj):

    # Title -------------------------------------------------------------------------------
    # Catagory
    title = 'Fastest ' if submission_obj.base_category == 'FASTEST' else 'Smallest '
    
    # Door dimensions
    if submission_obj.door_width and submission_obj.door_height:
        title += '{}x{} '.format(submission_obj.door_width, submission_obj.door_height)
    elif submission_obj.door_width:
        title += '{} wide '.format(submission_obj.door_width)
    elif submission_obj.door_height:
        title += '{} high '.format(submission_obj.door_height)
    
    # First order restrictions
    for restriction in submission_obj.fo_restrictions:
        if restriction != 'None':
            title += '{} '.format(restriction)
    
    # Pattern
    if submission_obj.door_pattern[0] != 'Regular':
        for pattern in submission_obj.door_pattern:
            title += '{} '.format(pattern)

    # Door type
    if submission_obj.door_type == None:
        title += 'Door.'
    elif submission_obj.door_type == 'SKY':
        title += 'Skydoor.'
    elif submission_obj.door_type == 'TRAP':
        title += 'Trapdoor.'

    # Description -------------------------------------------------------------------------
    description = ''

    # Second order restrictions
    if submission_obj.so_restrictions[0] != 'None':
        description += ', '.join(submission_obj.so_restrictions)
        if submission_obj.information:
            description += '\n\n'
    if submission_obj.information:
        description += submission_obj.information

    # Embed -------------------------------------------------------------------------------
    em = discord.Embed(title = title, description = description, colour = utils.discord_green)

    em.add_field(name = 'Creators', value = ', '.join(submission_obj.creators), inline = True)
    em.add_field(name = 'Dimensions', value = '{}x{}x{}'.format(submission_obj.build_width, submission_obj.build_height, submission_obj.build_depth), inline = True)
    em.add_field(name = 'Volume', value = str(submission_obj.build_width * submission_obj.build_height * submission_obj.build_depth), inline = True)
    em.add_field(name = 'Closing Time', value = str(submission_obj.relative_close_time), inline = True)
    em.add_field(name = 'Opening Time', value = str(submission_obj.relative_open_time), inline = True)

    if submission_obj.server_ip:
        value = submission_obj.server_ip if not submission_obj.coordinates else '{} - {}'.format(submission_obj.server_ip, submission_obj.coordinates)
        em.add_field(name = 'Server', value = value, inline = True)
    if submission_obj.youtube_link:
        em.add_field(name = 'Video', value = submission_obj.youtube_link, inline = True)
    if submission_obj.world_download_link:
        em.add_field(name = 'World Download', value = submission_obj.world_download_link, inline = True)
    if submission_obj.command:
        em.add_field(name = 'Command', value = submission_obj.command, inline = True)

    if submission_obj.image_url:
        em.set_image(url = submission_obj.image_url)
    
    em.set_footer(text = 'Created at {}.'.format(submission_obj.build_date))

    return em