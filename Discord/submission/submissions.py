import os
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions

# Submissions Command Branch -----------------------------------------------------------------------------
SUBMISSIONS_COMMANDS = Command_Branch('View, confirm and deny submissions.')

submission_roles = ['Admin', 'Moderator']

# Open ---------------------------------------------------------------------------------------------------
async def open_function(client, user_command, message):
    # Sending working message.
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Getting information...'))

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
            desc.append('**{}** - {}\n_by {}_ - _submitted by {}_'.format(sub.id, sub.get_title(), ', '.join(sorted(sub.creators)), sub.submitted_by))
        desc = '\n\n'.join(desc)
    
    # Creating embed
    em = discord.Embed(title = 'Open Records', description = desc, colour = utils.discord_green)

    # Sending embed
    await client.delete_message(sent_message)
    return em

SUBMISSIONS_COMMANDS.add_command('open', Command_Leaf(open_function, 'Shows an overview of all submissions open for review.', roles = submission_roles))

# View ---------------------------------------------------------------------------------------------------
async def view_function(client, user_command, message):
    # Sending working message.
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Getting information...'))

    index = int(user_command.split(' ')[2])
    open_submissions = submissions.get_open_submissions()

    result = None
    for sub in open_submissions:
        if sub.id == index:
            result = sub
            break
        
    await client.delete_message(sent_message)
    if result == None:
        return utils.error_embed('Error', 'No open submission with that ID.')
    return post.generate_embed(result)

view_params = [
    Param('index', 'The id of the submission you wish to view.', dtype = 'int')
]

SUBMISSIONS_COMMANDS.add_command('view', Command_Leaf(view_function, 'Displays an open submission.', roles = submission_roles, params = view_params))

# Confirm ------------------------------------------------------------------------------------------------
async def confirm_submission(client, user_command, message):
    # Sending working message.
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Please wait...'))

    submission_id = int(user_command.split(' ')[2])
    sub = submissions.get_open_submission(submission_id)

    if sub == None:
        await client.delete_message(sent_message)
        await client.send_message(message.channel, embed = utils.error_embed('Error', 'No open submission with that ID.'))
        return

    await post.post_submission(client, sub)
    submissions.confirm_submission(sub.id)

    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Success', 'Submission has successfully been confirmed.'))

confirm_params = [
    Param('index', 'The id of the submission you wish to confirm.', dtype = 'int')
]

SUBMISSIONS_COMMANDS.add_command('confirm', Command_Leaf(confirm_submission, 'Marks a submission as confirmed.', roles = submission_roles, servers = [config.OWNER_SERVER_ID], params = confirm_params))

# Confirm ------------------------------------------------------------------------------------------------
async def deny_submission(client, user_command, message):
    # Sending working message.
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Please wait...'))

    submission_id = int(user_command.split(' ')[2])
    sub = submissions.get_open_submission(submission_id)

    if sub == None:
        await client.delete_message(sent_message)
        await client.send_message(message.channel, embed = utils.error_embed('Error', 'No open submission with that ID.'))
        return

    submissions.deny_submission(sub.id)

    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Success', 'Submission has successfully been denied.'))

deny_params = [
    Param('index', 'The id of the submission you wish to deny.', dtype = 'int')
]

SUBMISSIONS_COMMANDS.add_command('deny', Command_Leaf(deny_submission, 'Marks a submission as denied.', roles = submission_roles, servers = [config.OWNER_SERVER_ID], params = deny_params))