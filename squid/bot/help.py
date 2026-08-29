"""Defines how the help command works in the bot."""

from collections.abc import Mapping, Sequence
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Protocol, cast, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import CardField, CardSection, card_node, error_node, render_payload, tr
from squid.config import BuildConfig
from squid.suggestions.application import candidate, rank
from squid.suggestions.domain import MAX_SUGGESTIONS
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

MORE_INFORMATION = tr(t"Use `/help <command>` to get more information.")

DIRECTORY_CATEGORIES: tuple[tuple[Any, frozenset[str]], ...] = (
    (tr(t"Build"), frozenset({"build"})),
    (tr(t"Discover"), frozenset({"search", "tags"})),
    (tr(t"Account"), frozenset({"account", "notifications"})),
    (tr(t"Community"), frozenset({"poll"})),
    (
        tr(t"Administration & setup"),
        frozenset({"access", "errors", "records", "redstoner", "settings", "starboard", "versions"}),
    ),
    (tr(t"Information"), frozenset({"help"})),
)
"""How the directory groups top-level commands, by command name.

Out here rather than inline so a name that no longer exists is a failing test instead of a
silently empty category — `patterns` and `vote` both outlived their commands in this map.
Staff groups remain listed because the help screen already respects Discord's command visibility.
"""


type AnyCommand = Command[Any, ..., Any] | app_commands.Command[Any, ..., Any] | app_commands.Group

SUBMISSION_FORM_URL = "https://forms.gle/i9Nf6apGgPGTUohr9"
REGULATIONS_URL = "https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit"
PROJECT_URL = "https://github.com/redstone-squid/Redstone-Squid"


class HelpClient(Protocol):
    """Bot facts rendered by the help screen."""

    @property
    def source_code_url(self) -> str | None: ...

    @property
    def user(self) -> discord.ClientUser | None: ...


class HelpScreen(sd.Screen):
    """A command browser that ends when closed, replaced, or timed out."""

    session_name = "help"
    timeout = 300
    visibility = "public"

    def __init__(self, bot: HelpClient, commands_: Sequence[AnyCommand], command: str | None) -> None:
        self._bot = bot
        self._commands = tuple(commands_)
        self._needle = command
        self._focused = self._find(command) if command is not None else None
        self._browser = sp.Browser(
            sl.sources.list_source(self._commands),
            key="commands",
            identity=lambda item: item.qualified_name,
            label=lambda item: f"/{item.qualified_name}",
            summary=lambda item: getattr(item, "short_doc", None) or getattr(item, "description", ""),
            detail=self._detail,
            page_size=10,
            title=tr(t"Redstone Squid help"),
            empty=tr(t"No commands are available."),
        )

    def _find(self, needle: str | None) -> AnyCommand | None:
        if needle is None:
            return None
        folded = needle.casefold().removeprefix("/")
        return next((item for item in self._commands if item.qualified_name.casefold() == folded), None)

    def _detail(self, command: AnyCommand) -> sl.LayoutNode[sl.ComponentsV2Target]:
        signature = getattr(command, "signature", "")
        qualified_name = command.qualified_name
        heading = f"/{qualified_name}{f' {signature}' if signature else ''}"
        description = (
            getattr(command, "help", None) or getattr(command, "description", None) or tr(t"No details provided")
        )
        children = tuple(getattr(command, "commands", ()))
        return sl.section(
            sl.heading(heading),
            sl.truncate(sl.paragraph(description)),
            sl.bullets(
                *(sl.bullet(f"/{child.qualified_name}") for child in children),
                key="subcommands",
            )
            if children
            else None,
        )

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._needle is not None and self._focused is None:
            needle = self._needle
            body: sl.LayoutNode[sl.ComponentsV2Target] = sl.section(
                sl.heading(tr(t"Command not found")),
                sl.paragraph(tr(t"No command named `{needle}` is available.")),
            )
        elif self._focused is not None:
            body = self._detail(self._focused)
        else:
            body = self.boundary(self._browser, key="browser")
        project_url = self._bot.source_code_url or PROJECT_URL
        links: list[sl.semantic.Link] = [
            sl.link(tr(t"Source"), project_url, key="source"),
            sl.link(tr(t"Submission form"), SUBMISSION_FORM_URL, key="form"),
            sl.link(tr(t"Documentation"), f"{project_url}/tree/master/docs", key="docs"),
            sl.link(tr(t"Regulations"), REGULATIONS_URL, key="regulations"),
        ]
        if self._bot.user is not None:
            links.insert(
                0,
                sl.link(
                    tr(t"Invite"),
                    f"https://discordapp.com/oauth2/authorize?client_id={self._bot.user.id}&scope=bot&permissions=8",
                    key="invite",
                ),
            )
        return (
            body,
            sl.action_controls(*links, key="help-links"),
            sl.action_controls(sl.action_control(tr(t"Close"), self._close, key="close"), key="help-actions"),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


def _summary(command: AnyCommand) -> str:
    """One line about a command, from wherever that surface keeps it.

    A prefix command carries `short_doc`, an app command a `description`. The directory
    lists both, because the app-only ones are exactly the commands a user cannot discover
    any other way.
    """
    text = getattr(command, "short_doc", None) or getattr(command, "description", "")
    return text or tr("No details provided")


def _command_section(
    title: str,
    commands_: Sequence[AnyCommand],
) -> CardSection:
    """Render a compact command category for the slash-help directory."""
    return CardSection(
        title,
        tuple(CardField(f"/{command.qualified_name}", _summary(command)) for command in commands_),
    )


class HelpCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Show help for a command or a group of commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.bot.help_command = Help()

    # /help [command]
    @app_commands.command()
    @app_commands.describe(command=app_commands.locale_str("The command to get help for."))
    async def help(self, interaction: discord.Interaction[BotT], command: str | None):
        """Show a grouped command directory or focused command details."""
        await HelpScreen(self.bot, self._all_commands(), command).show(interaction)

    def _all_commands(self) -> list[AnyCommand]:
        """Every command in either public tree, de-duplicated by qualified name."""
        commands_: list[AnyCommand] = list(self.bot.walk_commands())
        known = {item.qualified_name for item in commands_}
        for root in self.bot.tree.get_commands(type=discord.AppCommandType.chat_input):
            app_commands_ = (root, *root.walk_commands()) if isinstance(root, app_commands.Group) else (root,)
            for item in app_commands_:
                if item.qualified_name not in known:
                    commands_.append(item)
                    known.add(item.qualified_name)
        return commands_

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
        commands_ = list(self.context.bot.commands)

        # We do not filter commands here, because it is too slow.
        # Every command needs to run its own checks even if the same check is used.
        # filtered_commands = await self.filter_commands(commands_, sort=True)
        desc = dedent(
            tr(
                "{description}\n\nCommands:{commands}\n\n{more_information}\n",
                description=self.context.bot.description,
                commands=self.get_commands_brief_details(commands_),
                more_information=tr(MORE_INFORMATION),
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
        await send_to(self.get_destination())(render_payload([card_node(tr("Help"), desc, footer=footer)]))

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        await send_to(self.get_destination())(
            render_payload(
                [
                    card_node(
                        tr("Command Help - `{name}`", name=command.qualified_name),
                        command.help or tr("No details provided"),
                    )
                ]
            )
        )

    @staticmethod
    def get_commands_brief_details(
        commands_: Sequence[Command[Any, Any, Any]], return_as_list: bool = False
    ) -> list[str] | str:
        """
        Formats the prefix, command name and signature, and short doc for an iterable of commands.

        return_as_list is helpful for passing these command details into the paginator as a list of command details.
        """
        no_details = tr("No details provided")
        details: list[str] = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(f"\n`{command.qualified_name}{signature}` - {command.short_doc or no_details}")
        if return_as_list:
            return details
        return "".join(details)

    @staticmethod
    def get_cog_brief_details(cogs: Sequence[Cog], return_as_list: bool = False) -> list[str] | str:
        no_details = tr("No details provided")
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

        command_details = self.get_commands_brief_details(list(commands_))
        desc = tr(
            "{description}\n\nUsable Subcommands: {commands}\n\n{more_information}",
            description=group.cog.description,
            commands=command_details or tr("None"),
            more_information=tr(MORE_INFORMATION),
        )
        await send_to(self.get_destination())(render_payload([card_node(tr("Command Help"), desc)]))
        return None

    # !help <cog>
    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        """Sends help for a cog."""
        commands_ = cog.walk_commands()
        command_details = self.get_commands_brief_details(list(commands_))
        desc = tr(
            "{description}\n\nUsable Subcommands:{commands}\n\n{more_information}",
            description=cog.description,
            commands=command_details or tr("None"),
            more_information=tr(MORE_INFORMATION),
        )
        await send_to(self.get_destination())(render_payload([card_node(tr("Command Help"), desc)]))

    @override
    async def command_not_found(self, string: str, /) -> str:  # type: ignore  # overriding a sync method
        return tr(
            "Unable to find command `{name}`. Use /help to get a list of available commands.",
            name=string,
        )

    @override
    async def send_error_message(self, error: str, /) -> None:  # type: ignore  # overriding a sync method
        # TODO: error can be a custom Error too
        await send_to(self.get_destination())(render_payload([error_node(tr("Error."), error)]))


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
