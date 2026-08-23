"""Defines how the help command works in the bot."""

from collections.abc import Mapping, Sequence
from textwrap import dedent
from typing import TYPE_CHECKING, Any, cast, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group

from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import CardField, CardSection, error_layout, help_layout, respond_presentation, send_to
from squid.config import BuildConfig
from squid.core.i18n import _
from squid.suggestions.application import candidate, rank
from squid.suggestions.domain import MAX_SUGGESTIONS

if TYPE_CHECKING:
    import squid.bot.app

MORE_INFORMATION = _("Use `/help <command>` to get more information.")

DIRECTORY_CATEGORIES: tuple[tuple[Any, frozenset[str]], ...] = (
    (_("Build"), frozenset({"build"})),
    (_("Discover"), frozenset({"search", "restrictions"})),
    (_("Account"), frozenset({"account", "notifications", "redstoner"})),
    (_("Community"), frozenset({"poll", "archive"})),
    (_("Administration & setup"), frozenset({"records", "settings", "tag", "version"})),
    (_("Information"), frozenset({"help", "info"})),
)
"""How the directory groups top-level commands, by command name.

Out here rather than inline so a name that no longer exists is a failing test instead of a
silently empty category — `patterns` and `vote` both outlived their commands in this map.
Staff groups are deliberately absent: they are hidden from non-staff pickers anyway, and a
directory that lists what most readers cannot run is the surface phase 5 is shrinking.
"""


type AnyCommand = Command[Any, ..., Any] | app_commands.Command[Any, ..., Any] | app_commands.Group


def _summary(command: AnyCommand, locale: str | None) -> str:
    """One line about a command, from wherever that surface keeps it.

    A prefix command carries `short_doc`, an app command a `description`. The directory
    lists both, because the app-only ones are exactly the commands a user cannot discover
    any other way.
    """
    text = getattr(command, "short_doc", None) or getattr(command, "description", "")
    return text or t(locale, _("No details provided"))


def _command_section(
    title: str,
    commands_: Sequence[AnyCommand],
    locale: str | None,
) -> CardSection:
    """Render a compact command category for the slash-help directory."""
    return CardSection(
        title,
        tuple(CardField(f"/{command.qualified_name}", _summary(command, locale)) for command in commands_),
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
                    await respond_presentation(
                        interaction,
                        error_layout(
                            t(locale, _("Command not found")),
                            t(locale, _("No command named `{name}` is available."), name=command),
                        ),
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
            root_commands = self._root_commands()
            sections = tuple(
                _command_section(t(locale, title), [item for item in root_commands if item.name in names], locale)
                for title, names in DIRECTORY_CATEGORIES
            )
            layout = help_layout(
                t(locale, _("Redstone Squid help")),
                t(locale, _("Choose a workflow, then use `/help command` for syntax and details.")),
                sections=sections,
                footer=t(locale, MORE_INFORMATION),
            )
        await respond_presentation(interaction, layout, ephemeral=False)

    def _root_commands(self) -> list[AnyCommand]:
        """Every top-level command a user could run, prefix tree and app tree alike.

        The directory used to read `bot.commands` alone, which meant an app-only command
        was undiscoverable from the one surface built for discovery — `/poll`, `/help`
        and `/notifications` were all missing. Hybrid commands appear in both trees, so the
        prefix spelling wins and the app tree only contributes what it alone has.
        """
        prefix_commands = [item for item in self.bot.commands if not item.hidden]
        named = {item.name for item in prefix_commands}
        app_tree = self.bot.tree.get_commands(type=discord.AppCommandType.chat_input)
        app_only = [item for item in app_tree if item.name not in named]
        return [*prefix_commands, *app_only]

    @help.autocomplete("command")
    async def command_autocomplete(
        self, _interaction: discord.Interaction[BotT], needle: str
    ) -> list[app_commands.Choice[str]]:
        """Complete a command or cog name.

        Not a registry source: the candidates are this process's loaded command tree, which no
        other surface can see. It still ranks through the shared matcher so `/help` orders results
        the same way every other autocomplete does.
        """
        if not needle:
            return [
                app_commands.Choice(name=cog_name, value=cog_name)
                for cog_name, cog in self.bot.cogs.items()
                if cog.get_commands()
            ][:MAX_SUGGESTIONS]

        candidates = [candidate(command.qualified_name) for command in self.bot.walk_commands()]
        return [
            app_commands.Choice(name=match.label, value=match.value)
            for match in rank(needle, candidates, limit=MAX_SUGGESTIONS)
        ]


class Help(commands.MinimalHelpCommand):
    """Show help for a command or a group of commands."""

    def __init__(self):
        super().__init__(command_attrs={"help": "Show help for a command or a group of commands."})

    @property
    def _bot(self) -> squid.bot.app.RedstoneSquid:
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
        build_config = getattr(self.context.bot, "build_config", None)
        if (
            isinstance(build_config, BuildConfig)
            and build_config.commit_hash is not None
            and build_config.commit_message is not None
        ):
            footer = f"commit: {build_config.commit_hash[:7]}, message: {build_config.commit_message.strip()}"
        await send_to(self.get_destination())(
            help_layout(t(locale, _("Help")), desc, footer=footer)
        )

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        locale = await resolve_locale(self.context, self._bot.services.settings)
        await send_to(self.get_destination())(
            help_layout(
                t(locale, _("Command Help - `{name}`"), name=command.qualified_name),
                command.help or t(locale, _("No details provided")),
            )
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
        await send_to(self.get_destination())(help_layout(t(locale, _("Command Help")), desc))
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
        await send_to(self.get_destination())(help_layout(t(locale, _("Command Help")), desc))

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
        await send_to(self.get_destination())(error_layout(t(locale, _("Error.")), error))


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
