"""Defines how the help command works in the bot."""

import os
from collections.abc import Mapping, Sequence
from textwrap import dedent
from typing import Any, override

import discord
import git
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Context, Group
from rapidfuzz import process

from squid.bot import utils

MORE_INFORMATION = "Use `/help <command>` to get more information."


class HelpCog[BotT: commands.Bot](Cog):
    """Show help for a command or a group of commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.bot.help_command = Help()

    # /help [command]
    @app_commands.command()
    @app_commands.describe(command="The command to get help for.")
    async def help(self, interaction: discord.Interaction[BotT], command: str | None):
        """Show help for a command or a group of commands."""
        # We are using a hack to make this slash command:
        #
        # The ctx.send_help method is supposed to be used in a prefix command and do not handle interactions,
        # this means that we intentionally send an empty message to make discord think that the interaction is handled,
        # and then we use ctx.send_help to send the help message, which just sends a message to the channel
        # instead of replying to the interaction.
        #
        # The end result is that we sent two messages, one empty ephemeral message to handle the interaction,
        # and one message with the help information.
        await interaction.response.send_message(content="loading...", ephemeral=True, delete_after=0, silent=True)
        ctx = await self.bot.get_context(interaction, cls=Context[BotT])
        if command is not None:
            await ctx.send_help(command)
        else:
            await ctx.send_help()

    @help.autocomplete("command")
    async def command_autocomplete(
        self, _interaction: discord.Interaction[BotT], needle: str
    ) -> list[app_commands.Choice[str]]:
        if not needle:
            return [
                app_commands.Choice(name=cog_name, value=cog_name)
                for cog_name, cog in self.bot.cogs.items()
                if cog.get_commands()
            ][:25]

        commands = [command.qualified_name for command in self.bot.walk_commands()]

        matches = process.extract(
            needle,
            commands,
            limit=25,
            score_cutoff=30,
        )
        return [app_commands.Choice(name=match[0], value=match[0]) for match in matches]


class Help(commands.MinimalHelpCommand):
    """Show help for a command or a group of commands."""

    def __init__(self):
        super().__init__(command_attrs={"help": "Show help for a command or a group of commands."})

    # !help
    @override
    async def send_bot_help(self, mapping: Mapping[Cog | None, list[Command[Any, ..., Any]]], /) -> None:
        commands_ = list(self.context.bot.commands)

        # We do not filter commands here, because it is too slow.
        # Every command needs to run its own checks even if the same check is used.
        # filtered_commands = await self.filter_commands(commands_, sort=True)
        desc = dedent(
            f"""\
            {self.context.bot.description}

            Commands:{self.get_commands_brief_details(commands_)}

            {MORE_INFORMATION}
            """
        )
        em = utils.help_embed("Help", desc)

        try:
            repo = git.Repo(search_parent_directories=True)
            em.set_footer(text=f"commit: {repo.head.commit.hexsha[:7]}, message: {repo.head.commit.message.strip()}")
        except git.InvalidGitRepositoryError:
            # If the repo is not a git repository, we can still use environment variables if available
            # Usually this is because the bot is running in a container
            git_commit_hash = os.getenv("GIT_COMMIT_HASH")
            git_commit_message = os.getenv("GIT_COMMIT_MESSAGE")
            if git_commit_hash is not None and git_commit_message is not None:
                em.set_footer(text=f"commit: {git_commit_hash[:7]}, message: {git_commit_message.strip()}")
        await self.get_destination().send(embed=em)

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        em = utils.help_embed(
            f"Command Help - `{command.qualified_name}`",
            f"{command.help or 'No details provided'}",
        )
        await self.get_destination().send(embed=em)

    @staticmethod
    def get_commands_brief_details(
        commands_: Sequence[Command[Any, Any, Any]], return_as_list: bool = False
    ) -> list[str] | str:
        """
        Formats the prefix, command name and signature, and short doc for an iterable of commands.

        return_as_list is helpful for passing these command details into the paginator as a list of command details.
        """
        details: list[str] = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(f"\n`{command.qualified_name}{signature}` - {command.short_doc or 'No details provided'}")
        if return_as_list:
            return details
        return "".join(details)

    @staticmethod
    def get_cog_brief_details(cogs: Sequence[Cog], return_as_list: bool = False) -> list[str] | str:
        details: list[str] = [f"\n`{cog.qualified_name}` - {cog.description or 'No details provided'}" for cog in cogs]
        if return_as_list:
            return details
        return "".join(details)

    # !help <group>
    # In our case, send_cog_help is the same as send_group_help, since every group is defined in a cog class under the same name.
    # In general though, @group may be used outside a cog, and in that case, send_cog_help would be different.
    @override
    async def send_group_help(self, group: Group[Any, ..., Any], /) -> None:
        """Sends help for a group command."""
        commands_ = group.commands

        if len(commands_) == 0:
            # Group is a subclass of Command
            # noinspection PyTypeChecker
            return await self.send_command_help(group)

        command_details = self.get_commands_brief_details(list(commands_))
        desc = f"""{group.cog.description}

            Usable Subcommands: {command_details or "None"}

            {MORE_INFORMATION}"""
        em = utils.help_embed("Command Help", desc)
        await self.get_destination().send(embed=em)
        return None

    # !help <cog>
    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        """Sends help for a cog."""
        commands_ = cog.walk_commands()
        command_details = self.get_commands_brief_details(list(commands_))
        desc = f"""{cog.description}

            Usable Subcommands:{command_details or "None"}

            {MORE_INFORMATION}"""
        em = utils.help_embed("Command Help", desc)
        await self.get_destination().send(embed=em)

    @override
    async def command_not_found(self, string: str, /) -> str:  # type: ignore  # overriding a sync method
        return f"Unable to find command `{string}`. Use /help to get a list of available commands."

    @override
    async def send_error_message(self, error: str, /) -> None:  # type: ignore  # overriding a sync method
        # TODO: error can be a custom Error too
        embed = utils.error_embed("Error.", error)
        await self.get_destination().send(embed=embed)


async def setup(bot: commands.Bot):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
