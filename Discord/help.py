from collections.abc import Mapping, Sequence
from typing import Optional, List, Any, override

import discord
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group

from Discord import utils
from Discord.config import PREFIX

MORE_INFORMATION = f"Use `{PREFIX}help <command>` to get more information."

class Help(commands.MinimalHelpCommand):
    """Show help for a command or a group of commands."""
    def __init__(self):
        super().__init__(command_attrs={'help': 'Show help for a command or a group of commands.'})
        # self.verify_checks = False

    # !help
    @override
    async def send_bot_help(self, mapping: Mapping[Optional[Cog], List[Command[Any, ..., Any]]], /) -> None:
        desc = f"""{self.context.bot.description}
        
        Commands:{self.get_commands_brief_details(list(self.context.bot.commands))}
        
        {MORE_INFORMATION}"""
        em = utils.help_embed("Help", desc)
        await self.context.send(embed=em)

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        em = utils.help_embed(f"Command Help - `{command.qualified_name}`", f"{command.help or 'No details provided'}")
        await self.context.send(embed=em)

    @staticmethod
    def get_commands_brief_details(commands_: Sequence[Command], return_as_list: bool = False) -> list[str] | str:
        """
        Formats the prefix, command name and signature, and short doc for an iterable of commands.

        return_as_list is helpful for passing these command details into the paginator as a list of command details.
        """
        details = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(
                f"\n`{command.qualified_name}{signature}` - {command.short_doc or 'No details provided'}"
            )
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
        desc = \
            f"""{group.cog.description}
            
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
        desc = \
            f"""{cog.description}
            
            Usable Subcommands:{command_details or "None"}
            
            {MORE_INFORMATION}"""
        em = utils.help_embed("Command Help", desc)
        await self.context.send(embed=em)

    @override
    async def command_not_found(self, string: str, /) -> str:
        return f'Unable to find command {PREFIX}{string}. Use {PREFIX}help to get a list of available commands.'

    @override
    async def send_error_message(self, error: str, /) -> None:
        # TODO: error can be a custom Error too
        embed = utils.error_embed('Error.', error)
        channel = self.get_destination()
        await channel.send(embed=embed)
