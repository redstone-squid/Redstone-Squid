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
import Database.message as msg

# Submissions Command Branch -----------------------------------------------------------------------------
SUBMISSIONS_COMMANDS = Command_Branch('View, confirm and deny submissions.')

submission_roles = ['Admin', 'Moderator']
submission_perms = [ADMINISTRATOR]


# Open ---------------------------------------------------------------------------------------------------
async def open_function(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Getting information...'))

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
            desc.append('**{}** - {}\n_by {}_ - _submitted by {}_'.format(sub.id, sub.get_title(),
                                                                          ', '.join(sorted(sub.creators)),
                                                                          sub.submitted_by))
        desc = '\n\n'.join(desc)

    # Creating embed
    em = discord.Embed(title='Open Records', description=desc, colour=utils.discord_green)

    # Sending embed
    await sent_message.delete()
    return em


SUBMISSIONS_COMMANDS.add_command('open',
                                 Command_Leaf(open_function, 'Shows an overview of all submissions open for review.',
                                              roles=submission_roles))


# View ---------------------------------------------------------------------------------------------------
async def view_function(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Getting information...'))

    index = int(user_command.split(' ')[2])
    open_submissions = submissions.get_open_submissions()

    result = None
    for sub in open_submissions:
        if sub.id == index:
            result = sub
            break

    await sent_message.delete()
    if result is None:
        return utils.error_embed('Error', 'No open submission with that ID.')
    return post.generate_embed(result)


view_params = [
    Param('index', 'The id of the submission you wish to view.', dtype='int')
]

SUBMISSIONS_COMMANDS.add_command('view',
                                 Command_Leaf(view_function, 'Displays an open submission.', roles=submission_roles,
                                              params=view_params))


# Confirm ------------------------------------------------------------------------------------------------
async def confirm_submission(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Please wait...'))

    submission_id = int(user_command.split(' ')[2])
    sub = submissions.get_open_submission(submission_id)

    if sub is None:
        await sent_message.delete()
        await message.channel.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return

    await post.post_submission(client, sub)
    submissions.confirm_submission(sub.id)

    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Success', 'Submission has successfully been confirmed.'))


confirm_params = [
    Param('index', 'The id of the submission you wish to confirm.', dtype='int')
]

SUBMISSIONS_COMMANDS.add_command('confirm', Command_Leaf(confirm_submission, 'Marks a submission as confirmed.',
                                                         roles=submission_roles, servers=[config.OWNER_SERVER_ID],
                                                         params=confirm_params))


# Deny ---------------------------------------------------------------------------------------------------
async def deny_submission(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Please wait...'))

    submission_id = int(user_command.split(' ')[2])
    sub = submissions.get_open_submission(submission_id)

    if sub is None:
        await sent_message.delete()
        await message.channel.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return

    submissions.deny_submission(sub.id)

    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Success', 'Submission has successfully been denied.'))


deny_params = [
    Param('index', 'The id of the submission you wish to deny.', dtype='int')
]

SUBMISSIONS_COMMANDS.add_command('deny',
                                 Command_Leaf(deny_submission, 'Marks a submission as denied.', roles=submission_roles,
                                              servers=[config.OWNER_SERVER_ID], params=deny_params))


# Outdated -----------------------------------------------------------------------------------------------
async def outdated_function(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Getting information...'))

    # Creating list of submissions
    outdated_submissions = msg.get_outdated_messages(message.server.id)

    desc = None
    if len(outdated_submissions) == 0:
        desc = 'No outdated submissions.'
    else:
        desc = []
        for sub in outdated_submissions:
            sub_obj = sub[1]
            desc.append('**{}** - {}\n_by {}_ - _submitted by {}_'.format(sub_obj.id, sub_obj.get_title(),
                                                                          ', '.join(sorted(sub_obj.creators)),
                                                                          sub_obj.submitted_by))
        desc = '\n\n'.join(desc)

    # Creating embed
    em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)

    # Sending embed
    await sent_message.delete()
    return em


SUBMISSIONS_COMMANDS.add_command('outdated', Command_Leaf(outdated_function,
                                                          'Shows an overview of all discord posts that are require updating.',
                                                          perms=submission_perms, roles=submission_roles,
                                                          perm_role_operator='Or'))


# Update -------------------------------------------------------------------------------------------------
async def update_function(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Updating information...'))

    submission_id = int(user_command.split(' ')[2])
    outdated_submissions = msg.get_outdated_messages(message.server.id)

    sub = None
    for outdated_sub in outdated_submissions:
        if outdated_sub[1].id == submission_id:
            sub = outdated_sub
            break

    if sub is None:
        await sent_message.delete()
        await message.channel.send(embed=utils.error_embed('Error', 'No outdated submissions with that ID.'))
        return

    if sub[0] is None:
        await post.post_submission_to_server(client, sub[1], message.server.id)
    else:
        await post.edit_post(client, message.server, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Success', 'Post has successfully been updated.'))


update_params = [
    Param('index', 'The id of the submission you wish to deny.', dtype='int')
]

SUBMISSIONS_COMMANDS.add_command('update', Command_Leaf(update_function, 'Updated an outdated discord post.',
                                                        perms=submission_perms, params=update_params,
                                                        roles=submission_roles, perm_role_operator='Or'))


# Update All ---------------------------------------------------------------------------------------------
async def update_all_function(client, user_command, message):
    # Sending working message.
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Updating information...'))

    outdated_submissions = msg.get_outdated_messages(message.server.id)

    for sub in outdated_submissions:
        if sub[0] is None:
            await post.post_submission_to_server(client, sub[1], message.server.id)
        else:
            await post.edit_post(client, message.server, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Success', 'All posts have been successfully updated.'))


SUBMISSIONS_COMMANDS.add_command('update_all', Command_Leaf(update_all_function, 'Updates all outdated discord posts.',
                                                            perms=submission_perms, roles=submission_roles,
                                                            perm_role_operator='Or'))
