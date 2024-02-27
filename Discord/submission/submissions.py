import discord
from discord.ext.commands import group, GroupCog, Context, has_any_role

import Discord.utils as utils
from Discord.permissions import *

import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions
import Database.message as msg


submission_roles = ['Admin', 'Moderator', 'Redstoner']
# submission_roles = ["Everyone"]

class Submissions(GroupCog, name='submissions'):
    """View, confirm and deny submissions."""
    def __init__(self, bot):
        self.bot = bot

    # Not sure if this works
    # def cog_check(self, ctx):
    #     """This is a check that will be called before any command in this cog is executed."""
    #     return has_any_role(*submission_roles)(lambda x: True)(ctx)

    @group(invoke_without_command=True, hidden=True)
    async def submissions(self, ctx: Context):
        """View, confirm and deny submissions."""
        await ctx.send_help(ctx.command)

    @submissions.command(name='open')
    @has_any_role(*submission_roles)
    async def open_function(self, ctx: Context):
        """Shows an overview of all submissions open for review."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

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
                # ID - Title
                # by Creators - submitted by Submitter
                desc.append(f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators))}_ - _submitted by {sub.submitted_by}_")
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Open Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.delete()
        await ctx.send(embed=em)

    @submissions.command(name='view')
    @has_any_role(*submission_roles)
    async def view_function(self, ctx: Context, index: int):
        """Displays an open submission."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        open_submissions = submissions.get_open_submissions()

        result = None
        for sub in open_submissions:
            if sub.id == index:
                result = sub
                break

        await sent_message.delete()
        if result is None:
            return await ctx.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        return await ctx.send(embed=post.generate_embed(result))

    # confirm_function
    @submissions.command(name='confirm')
    @has_any_role(*submission_roles)
    async def confirm_function(self, ctx: Context, index: int):
        """Marks a submission as confirmed."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        sub = submissions.get_open_submission(index)

        if sub is None:
            await sent_message.delete()
            return await ctx.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        await post.post_submission(self.bot, sub)
        submissions.confirm_submission(sub.id)

        await sent_message.delete()
        return await ctx.send(embed=utils.info_embed('Success', 'Submission has successfully been confirmed.'))

    @submissions.command(name='deny')
    @has_any_role(*submission_roles)
    async def deny_function(self, ctx: Context, index: int):
        """Marks a submission as denied."""
        if not ctx.guild.id == config.OWNER_SERVER_ID:
            em = utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')
            return await ctx.send(embed=em)

        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Please wait...'))

        sub = submissions.get_open_submission(index)

        if sub is None:
            await sent_message.delete()
            return await ctx.send(embed=utils.error_embed('Error', 'No open submission with that ID.'))
        submissions.deny_submission(sub.id)

        await sent_message.delete()
        return await ctx.send(embed=utils.info_embed('Success', 'Submission has successfully been denied.'))

    @submissions.command(name='outdated')
    @has_any_role(*submission_roles)
    async def outdated_function(self, ctx: Context):
        """Shows an overview of all discord posts that require updating."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        # Creating list of submissions
        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        desc = None
        if len(outdated_submissions) == 0:
            desc = 'No outdated submissions.'
        else:
            desc = []
            for sub in outdated_submissions:
                sub_obj = sub[1]
                desc.append(f"**{sub_obj.id}** - {sub_obj.get_title()}\n_by {', '.join(sorted(sub_obj.creators))}_ - _submitted by {sub_obj.submitted_by}_")
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.delete()
        return await ctx.send(embed=em)

    @submissions.command(name='update')
    @has_any_role(*submission_roles)
    async def update_function(self, ctx, index: int):
        """Updated an outdated discord post."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        submission_id = index
        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        sub = None
        for outdated_sub in outdated_submissions:
            if outdated_sub[1].id == submission_id:
                sub = outdated_sub
                break

        if sub is None:
            await sent_message.delete()
            return await ctx.send(embed=utils.error_embed('Error', 'No outdated submissions with that ID.'))

        if sub[0] is None:
            await post.post_submission_to_server(self.bot, sub[1], ctx.guild.id)
        else:
            await post.edit_post(self.bot, ctx.guild, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

        await sent_message.delete()
        return await ctx.send(embed=utils.info_embed('Success', 'Post has successfully been updated.'))

    @submissions.command(name='update_all')
    @has_any_role(*submission_roles)
    async def update_all_function(self, ctx):
        """Updates all outdated discord posts."""
        # Sending working message.
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        outdated_submissions = msg.get_outdated_messages(ctx.guild.id)

        for sub in outdated_submissions:
            if sub[0] is None:
                await post.post_submission_to_server(self.bot, sub[1], ctx.guild.id)
            else:
                await post.edit_post(self.bot, ctx.guild, sub[0]['Channel ID'], sub[0]['Message ID'], sub[1])

        await sent_message.delete()
        return await ctx.send(embed=utils.info_embed('Success', 'All posts have been successfully updated.'))
