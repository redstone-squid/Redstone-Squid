import os
import discord
from discord.ext import commands

import Discord.utils as utils
from Discord.command_branch import CommandBranch
from Discord.permissions import *

import Discord.config as config
import Discord.submission.post as post
import Database.submissions as submissions
import Database.message as msg


submission_roles = ['Admin', 'Moderator']
submission_perms = [ADMINISTRATOR]


class Submissions(commands.GroupCog, name='Submissions'):
    def __init__(self, bot):
        self.bot = bot

    def cog_check(self, ctx):
        """This is a check that will be called before any command in this cog is executed."""
        return commands.has_any_role(*submission_roles)

    @commands.command(name='open', aliases=['o'], brief='Shows an overview of all submissions open for review.')
    async def open_function(self, ctx):
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
                desc.append('**{}** - {}\n_by {}_ - _submitted by {}_'.format(sub.id, sub.get_title(),
                                                                              ', '.join(sorted(sub.creators)),
                                                                              sub.submitted_by))
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Open Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.delete()
        await ctx.send(embed=em)

    @commands.command(name='view', brief='Displays an open submission.')
    async def view_function(self, ctx, index: int):
        """
        Displays an open submission.

        Args:
            ctx: The context of the command.
            index: The id of the submission you wish to view.

        Returns:
            None
        """
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
    @commands.command(name='confirm', brief='Marks a submission as confirmed.')
    async def confirm_function(self, ctx, index: int):
        """
        Marks a submission as confirmed.

        Args:
            ctx: The context of the command.
            index: The id of the submission you wish to confirm.

        Returns:
            None
        """
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

    @commands.command(name='deny', brief='Marks a submission as denied.')
    async def deny_function(self, ctx, index: int):
        """
        Marks a submission as denied.

        Args:
            ctx: The context of the command.
            index: The id of the submission you wish to deny.

        Returns:
            None
        """
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

    @commands.command(name='outdated', brief='Shows an overview of all discord posts that are require updating.')
    async def outdated_function(self, ctx):
        """
        Shows an overview of all discord posts that are require updating.

        Args:
            ctx: The context of the command.

        Returns:
            None
        """
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
                desc.append('**{}** - {}\n_by {}_ - _submitted by {}_'.format(sub_obj.id, sub_obj.get_title(),
                                                                              ', '.join(sorted(sub_obj.creators)),
                                                                              sub_obj.submitted_by))
            desc = '\n\n'.join(desc)

        # Creating embed
        em = discord.Embed(title='Outdated Records', description=desc, colour=utils.discord_green)

        # Sending embed
        await sent_message.delete()
        return await ctx.send(embed=em)

    @commands.command(name='update', brief='Updated an outdated discord post.')
    async def update_function(self, ctx, index: int):
        """
        Updated an outdated discord post.

        Args:
            ctx: The context of the command.
            index: The id of the submission you wish to deny.

        Returns:
            None
        """
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

    @commands.command(name='update_all', brief='Updates all outdated discord posts.')
    async def update_all_function(self, ctx):
        """
        Updates all outdated discord posts.

        Args:
            ctx: The context of the command.

        Returns:
            None
        """
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
