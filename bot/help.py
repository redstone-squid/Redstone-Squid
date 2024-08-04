from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, override, TYPE_CHECKING

import discord
from discord import app_commands, InteractionResponse
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group, Context

from bot import utils

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

MORE_INFORMATION = "Use `/help <command>` to get more information.\nNote that this command does not contain certain commands that are only usable as slash commands, like /submit"


class HelpCog(Cog):
    """Show help for a command or a group of commands."""

    def __init__(self, bot: RedstoneSquid):
        self.bot = bot
        self.bot.help_command = Help()

    # /help [command]
    @app_commands.command()
    async def help(self, interaction: discord.Interaction[RedstoneSquid], command: str | None):
        """Show help for a command or a group of commands."""
        # noinspection PyTypeChecker
        response: InteractionResponse = interaction.response
        await response.defer()
        ctx = await self.bot.get_context(interaction, cls=Context)
        if command is not None:
            await ctx.send_help(command)
        else:
            await ctx.send_help()

    @help.autocomplete("command")
    async def command_autocomplete(
        self, interaction: discord.Interaction, needle: str
    ) -> list[app_commands.Choice[str]]:
        assert self.bot.help_command
        ctx = await self.bot.get_context(interaction, cls=Context[RedstoneSquid])
        help_command = self.bot.help_command.copy()
        help_command.context = ctx
        if not needle:
            return [
                app_commands.Choice(name=cog_name, value=cog_name)
                for cog_name, cog in self.bot.cogs.items()
                if await help_command.filter_commands(cog.get_commands())
            ][:25]
        needle = needle.lower()
        return [
            app_commands.Choice(name=command.qualified_name, value=command.qualified_name)
            for command in await help_command.filter_commands(self.bot.walk_commands(), sort=True)
            if needle in command.qualified_name
        ][:25]


class Help(commands.MinimalHelpCommand):
    """Show help for a command or a group of commands."""

    def __init__(self):
        super().__init__(command_attrs={"help": "Show help for a command or a group of commands."})
        # self.verify_checks = False

    # !help
    @override
    async def send_bot_help(self, mapping: Mapping[Cog | None, list[Command[Any, ..., Any]]], /) -> None:
        # TODO: hide hidden commands
        commands_ = list(self.context.bot.commands)
        filtered_commands = await self.filter_commands(commands_, sort=True)
        desc = f"""{self.context.bot.description}

        Commands:{self.get_commands_brief_details(filtered_commands)}

        {MORE_INFORMATION}"""
        em = utils.help_embed("Help", desc)
        await self.context.send(embed=em)

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        em = utils.help_embed(
            f"Command Help - `{command.qualified_name}`",
            f"{command.help or 'No details provided'}",
        )
        await self.get_destination().send(embed=em)

    @staticmethod
    def get_commands_brief_details(commands_: Sequence[Command], return_as_list: bool = False) -> list[str] | str:
        """
        Formats the prefix, command name and signature, and short doc for an iterable of commands.

        return_as_list is helpful for passing these command details into the paginator as a list of command details.
        """
        details = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(f"\n`{command.qualified_name}{signature}` - {command.short_doc or 'No details provided'}")
        if return_as_list:
            return details
        return "".join(details)

    @staticmethod
    def get_cog_brief_details(cogs: Sequence[Cog], return_as_list: bool = False) -> list[str] | str:
        details: list[str] = []
        for cog in cogs:
            details.append(f"\n`{cog.qualified_name}` - {cog.description or 'No details provided'}")
        if return_as_list:
            return details
        return "".join(details)

    # !help <group>
    # In our case, send_cog_help is the same as send_group_help, since every group is defined in a cog class under the same name.
    # In general though, @group may be used outside a cog, and in that case, send_cog_help would be different.
    @override
    async def send_group_help(self, group: Group[Any, ..., Any], /) -> None:
        """Sends help for a group command."""
        subcommands = group.commands

        if len(subcommands) == 0:
            # Group is a subclass of Command
            # noinspection PyTypeChecker
            return await self.send_command_help(group)

        commands_ = await self.filter_commands(subcommands, sort=True)
        command_details = self.get_commands_brief_details(commands_)
        desc = f"""{group.cog.description}

            Usable Subcommands: {command_details or "None"}

            {MORE_INFORMATION}"""
        em = utils.help_embed("Command Help", desc)
        await self.get_destination().send(embed=em)

    # !help <cog>
    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        """Sends help for a cog."""
        commands_ = await self.filter_commands(cog.walk_commands(), sort=True)
        command_details = self.get_commands_brief_details(commands_)
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


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
