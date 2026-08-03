"""Defines how the help command works in the bot."""

from collections.abc import Mapping, Sequence
from textwrap import dedent
from typing import TYPE_CHECKING, Any, cast, override

import discord
import git
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group
from rapidfuzz import process

from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import CardField, CardSection, error_layout, help_layout, no_mentions
from squid.config import BuildConfig
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app

MORE_INFORMATION = _("Use `/help <command>` to get more information.")


def _command_section(
    title: str,
    commands_: Sequence[Command[Any, ..., Any]],
    locale: str | None,
) -> CardSection:
    """Render a compact command category for the slash-help directory."""
    return CardSection(
        title,
        tuple(
            CardField(f"/{command.qualified_name}", command.short_doc or t(locale, _("No details provided")))
            for command in commands_
        ),
    )


class HelpCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Show help for a command or a group of commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.bot.help_command = Help()

    # /help [command]
    @app_commands.command()
    @app_commands.describe(command=app_commands.locale_str(_("The command to get help for.")))
    async def help(self, interaction: discord.Interaction[BotT], command: str | None):
        """Show a grouped command directory or focused command details."""
        locale = await resolve_locale(interaction, interaction.client.services.settings)
        if command is not None:
            target = self.bot.get_command(command)
            if target is None:
                cog = next(
                    (item for item in self.bot.cogs.values() if item.qualified_name.casefold() == command.casefold()),
                    None,
                )
                candidates = list(cog.walk_commands()) if cog is not None else []
                if not candidates:
                    await interaction.response.send_message(
                        view=error_layout(
                            t(locale, _("Command not found")),
                            t(locale, _("No command named `{name}` is available."), name=command),
                        ),
                        allowed_mentions=no_mentions(),
                    )
                    return
                assert cog is not None
                layout = help_layout(
                    t(locale, _("{name} commands"), name=cog.qualified_name),
                    cog.description or t(locale, _("Commands in this area.")),
                    sections=(_command_section(t(locale, _("Commands")), candidates, locale),),
                    footer=t(locale, MORE_INFORMATION),
                )
            else:
                children = list(target.commands) if isinstance(target, Group) else []
                signature = f" {target.signature}" if target.signature else ""
                layout = help_layout(
                    f"/{target.qualified_name}{signature}",
                    target.help or t(locale, _("No details provided")),
                    sections=(_command_section(t(locale, _("Subcommands")), children, locale),) if children else (),
                    footer=t(locale, _("Command names and options also autocomplete in Discord.")),
                )
        else:
            categories = {
                t(locale, _("Build")): {"build"},
                t(locale, _("Discover")): {"search", "patterns", "restrictions"},
                t(locale, _("Account")): {"account", "redstoner"},
                t(locale, _("Staff & setup")): {"admin", "settings", "tag", "vote", "version"},
                t(locale, _("Information")): {"info", "archive"},
            }
            root_commands = [item for item in self.bot.commands if not item.hidden]
            sections = tuple(
                _command_section(title, [item for item in root_commands if item.name in names], locale)
                for title, names in categories.items()
            )
            layout = help_layout(
                t(locale, _("Redstone Squid help")),
                t(locale, _("Choose a workflow, then use `/help command` for syntax and details.")),
                sections=sections,
                footer=t(locale, MORE_INFORMATION),
            )
        await interaction.response.send_message(view=layout, allowed_mentions=no_mentions())

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

    @property
    def _bot(self) -> "squid.bot.app.RedstoneSquid":
        # discord.py's HelpCommand.context is generically typed as Context[Bot | AutoShardedBot];
        # the bot is always the concrete RedstoneSquid at runtime.
        return cast("squid.bot.app.RedstoneSquid", self.context.bot)

    # !help
    @override
    async def send_bot_help(self, mapping: Mapping[Cog | None, list[Command[Any, ..., Any]]], /) -> None:
        locale = await resolve_locale(self.context, self._bot.services.settings)
        commands_ = list(self.context.bot.commands)

        # We do not filter commands here, because it is too slow.
        # Every command needs to run its own checks even if the same check is used.
        # filtered_commands = await self.filter_commands(commands_, sort=True)
        desc = dedent(
            t(
                locale,
                _("{description}\n\nCommands:{commands}\n\n{more_information}\n"),
                description=self.context.bot.description,
                commands=self.get_commands_brief_details(commands_, locale=locale),
                more_information=t(locale, MORE_INFORMATION),
            )
        )
        footer: str | None = None
        try:
            repo = git.Repo(search_parent_directories=True)
            footer = f"commit: {repo.head.commit.hexsha[:7]}, message: {repo.head.commit.message.strip()}"
        except git.InvalidGitRepositoryError:
            build_config = getattr(self.context.bot, "build_config", None)
            if (
                isinstance(build_config, BuildConfig)
                and build_config.commit_hash is not None
                and build_config.commit_message is not None
            ):
                footer = f"commit: {build_config.commit_hash[:7]}, message: {build_config.commit_message.strip()}"
        await self.get_destination().send(
            view=help_layout(t(locale, _("Help")), desc, footer=footer),
            allowed_mentions=no_mentions(),
        )

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        locale = await resolve_locale(self.context, self._bot.services.settings)
        await self.get_destination().send(
            view=help_layout(
                t(locale, _("Command Help - `{name}`"), name=command.qualified_name),
                command.help or t(locale, _("No details provided")),
            ),
            allowed_mentions=no_mentions(),
        )

    @staticmethod
    def get_commands_brief_details(
        commands_: Sequence[Command[Any, Any, Any]], return_as_list: bool = False, locale: str | None = None
    ) -> list[str] | str:
        """
        Formats the prefix, command name and signature, and short doc for an iterable of commands.

        return_as_list is helpful for passing these command details into the paginator as a list of command details.
        """
        no_details = t(locale, _("No details provided"))
        details: list[str] = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(f"\n`{command.qualified_name}{signature}` - {command.short_doc or no_details}")
        if return_as_list:
            return details
        return "".join(details)

    @staticmethod
    def get_cog_brief_details(
        cogs: Sequence[Cog], return_as_list: bool = False, locale: str | None = None
    ) -> list[str] | str:
        no_details = t(locale, _("No details provided"))
        details: list[str] = [f"\n`{cog.qualified_name}` - {cog.description or no_details}" for cog in cogs]
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

        locale = await resolve_locale(self.context, self._bot.services.settings)
        command_details = self.get_commands_brief_details(list(commands_), locale=locale)
        desc = t(
            locale,
            _("{description}\n\nUsable Subcommands: {commands}\n\n{more_information}"),
            description=group.cog.description,
            commands=command_details or t(locale, _("None")),
            more_information=t(locale, MORE_INFORMATION),
        )
        await self.get_destination().send(
            view=help_layout(t(locale, _("Command Help")), desc),
            allowed_mentions=no_mentions(),
        )
        return None

    # !help <cog>
    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        """Sends help for a cog."""
        locale = await resolve_locale(self.context, self._bot.services.settings)
        commands_ = cog.walk_commands()
        command_details = self.get_commands_brief_details(list(commands_), locale=locale)
        desc = t(
            locale,
            _("{description}\n\nUsable Subcommands:{commands}\n\n{more_information}"),
            description=cog.description,
            commands=command_details or t(locale, _("None")),
            more_information=t(locale, MORE_INFORMATION),
        )
        await self.get_destination().send(
            view=help_layout(t(locale, _("Command Help")), desc),
            allowed_mentions=no_mentions(),
        )

    @override
    async def command_not_found(self, string: str, /) -> str:  # type: ignore  # overriding a sync method
        locale = await resolve_locale(self.context, self._bot.services.settings)
        return t(
            locale,
            _("Unable to find command `{name}`. Use /help to get a list of available commands."),
            name=string,
        )

    @override
    async def send_error_message(self, error: str, /) -> None:  # type: ignore  # overriding a sync method
        # TODO: error can be a custom Error too
        locale = await resolve_locale(self.context, self._bot.services.settings)
        await self.get_destination().send(
            view=error_layout(t(locale, _("Error.")), error),
            allowed_mentions=no_mentions(),
        )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
