"""Render command discovery, focused command help, and project links."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, Self, cast, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.i18n import to_locale_str
from squid.bot.ui import card_node, error_node, render_payload, tr
from squid.config import BuildConfig
from squid.suggestions.application import Candidate, rank
from squid.suggestions.domain import MAX_SUGGESTIONS
from squid_ui.text import Message, TextLike
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

MORE_INFORMATION = tr(t"help-details-hint = Use `/help <command>` to get more information.")

DIRECTORY_CATEGORIES: tuple[tuple[Any, frozenset[str]], ...] = (
    (tr(t"help-build = Build"), frozenset({"build"})),
    (tr(t"help-discover = Discover"), frozenset({"search", "tags"})),
    (tr(t"help-account = Account"), frozenset({"account", "notifications"})),
    (tr(t"help-community = Community"), frozenset({"poll", "recount"})),
    (
        tr(t"help-administration-setup = Administration & setup"),
        frozenset(
            {"access", "errors", "records", "redstoner", "runtime-settings", "settings", "starboard", "versions"}
        ),
    ),
    (tr(t"help-information = Information"), frozenset({"help"})),
)
"""Top-level command categories, kept separate so tests can check registered command names."""


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
    """A command browser that ends when replaced or timed out."""

    policy = sd.ResponseSpec(timeout=300, audience="public", session=sd.SessionSpec("help"))

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
            summary=lambda item: f"`/{item.qualified_name}` — " + _summary(item),
            detail=self._detail,
            page_size=10,
            title=tr(t"help-redstone-squid-help = Redstone Squid help"),
            empty=tr(t"help-no-commands-are-available = No commands are available."),
        )

    def _find(self, needle: str | None) -> AnyCommand | None:
        if needle is None:
            return None
        folded = needle.casefold().removeprefix("/")
        return next((item for item in self._commands if item.qualified_name.casefold() == folded), None)

    def _detail(self, command: AnyCommand) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        signature = getattr(command, "signature", "")
        qualified_name = command.qualified_name
        heading = f"/{qualified_name}{f' {signature}' if signature else ''}"
        description = (
            getattr(command, "help", None)
            or getattr(command, "description", None)
            or tr(t"help-no-details-provided = No details provided")
        )
        # Only the group variants of this union carry subcommands; the annotation keeps the
        # duck-typed lookup from collapsing to an empty tuple type.
        children: tuple[AnyCommand, ...] = tuple(getattr(command, "commands", ()))
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

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._needle is not None and self._focused is None:
            needle = self._needle
            body: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = sl.section(
                sl.heading(tr(t"help-command-not-found = Command not found")),
                sl.paragraph(tr(t"help-command-unavailable = No command named `{needle}` is available.")),
            )
        elif self._focused is not None:
            body = self._detail(self._focused)
        else:
            body = self._browser
        project_url = self._bot.source_code_url or PROJECT_URL
        links: list[sl.semantic.Link] = [
            sl.link(tr(t"help-source = Source"), project_url, key="source"),
            sl.link(tr(t"help-submission-form = Submission form"), SUBMISSION_FORM_URL, key="form"),
            sl.link(tr(t"help-documentation = Documentation"), f"{project_url}/tree/master/docs", key="docs"),
            sl.link(tr(t"help-regulations = Regulations"), REGULATIONS_URL, key="regulations"),
        ]
        if self._bot.user is not None:
            links.insert(
                0,
                sl.link(
                    tr(t"help-invite = Invite"),
                    f"https://discordapp.com/oauth2/authorize?client_id={self._bot.user.id}&scope=bot&permissions=8",
                    key="invite",
                ),
            )
        return (
            body,
            sl.action_controls(*links, key="help-links"),
        )


def _summary(command: AnyCommand) -> TextLike:
    """Return a prefix command's short documentation or an app command's description."""
    text = getattr(command, "short_doc", None) or getattr(command, "description", "")
    return text or tr(t"help-no-details-provided = No details provided")


class HelpCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Show help for a command or a group of commands."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.bot.help_command = Help()

    # /help [command]
    @sd.command()
    @app_commands.describe(
        command=to_locale_str(tr(t"help-the-command-to-get-help-for = The command to get help for."))
    )
    async def help(self, request: sd.Request[Self], command: str | None) -> HelpScreen:
        """Show a grouped command directory or focused command details."""
        return HelpScreen(self.bot, self._all_commands(), command)

    def _all_commands(self) -> list[AnyCommand]:
        """Combine loaded prefix and app commands, preferring prefix entries for duplicate names.

        This collection does not filter hidden commands or evaluate caller permissions.
        """
        commands_: list[AnyCommand] = list(self.bot.walk_commands())
        known = {item.qualified_name for item in commands_}
        for root in self.bot.tree.get_commands(type=discord.AppCommandType.chat_input):
            app_commands_ = (root, *root.walk_commands()) if isinstance(root, app_commands.Group) else (root,)
            for item in app_commands_:
                if item.qualified_name not in known:
                    commands_.append(item)
                    known.add(item.qualified_name)
        return commands_

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

        candidates = [Candidate.create(command.qualified_name) for command in self.bot.walk_commands()]
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

        # Avoid running each command's authorization checks just to render the directory.
        description = sl.raw_md(self.context.bot.description)
        commands = self.get_commands_brief_details(commands_)
        more_information = MORE_INFORMATION
        desc = tr(t"""
            bot-help-commands =
                {description}

                Commands:{commands}

                {more_information}{{ "\\u000A" }}
            """)
        footer: str | None = None
        build_config = getattr(self.context.bot, "build_config", None)
        if (
            isinstance(build_config, BuildConfig)
            and build_config.commit_hash is not None
            and build_config.commit_message is not None
        ):
            footer = f"commit: {build_config.commit_hash[:7]}, message: {build_config.commit_message.strip()}"
        await send_to(self.get_destination())(render_payload([card_node(tr(t"help-help = Help"), desc, footer=footer)]))

    # !help <command>
    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        name = command.qualified_name
        await send_to(self.get_destination())(
            render_payload(
                [
                    card_node(
                        tr(t"help-command-help = Command Help - `{name}`"),
                        command.help or tr(t"help-no-details-provided = No details provided"),
                    )
                ]
            )
        )

    @staticmethod
    def get_commands_brief_details(commands_: Sequence[Command[Any, Any, Any]]) -> Message:
        """Format names, signatures, and short descriptions in input order, each on a new line."""
        no_details = tr(t"help-no-details-provided = No details provided")
        details: list[TextLike] = []
        for command in commands_:
            signature = f" {command.signature}" if command.signature else ""
            details.append(f"\n`{command.qualified_name}{signature}` - " + (command.short_doc or no_details))
        return Message.join("", details)

    # Group help lists immediate children; cog help below walks all of the cog's commands.
    @override
    async def send_group_help(self, group: Group[Any, ..., Any], /) -> None:
        """Sends help for a group command."""
        commands_ = group.commands

        if len(commands_) == 0:
            # Group is a subclass of Command
            # noinspection PyTypeChecker
            return await self.send_command_help(group)

        listed = list(commands_)
        description = sl.raw_md(group.cog.description)
        commands = self.get_commands_brief_details(listed) if listed else tr(t"help-none = None")
        more_information = MORE_INFORMATION
        desc = tr(t"""
            bot-help-usable-subcommands =
                {description}

                Usable Subcommands: {commands}

                {more_information}
            """)
        await send_to(self.get_destination())(
            render_payload([card_node(tr(t"help-command-help-2 = Command Help"), desc)])
        )
        return None

    # !help <cog>
    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        """Sends help for a cog."""
        commands_ = cog.walk_commands()
        listed = list(commands_)
        description = sl.raw_md(cog.description)
        commands = self.get_commands_brief_details(listed) if listed else tr(t"help-none = None")
        more_information = MORE_INFORMATION
        desc = tr(t"""
            bot-help-usable-subcommands-2 =
                {description}

                Usable Subcommands:{commands}

                {more_information}
            """)
        await send_to(self.get_destination())(
            render_payload([card_node(tr(t"help-command-help-2 = Command Help"), desc)])
        )

    @override
    async def command_not_found(self, string: str, /) -> str:  # type: ignore  # overriding a sync method
        name = string
        # discord.py passes this string straight to send_error_message.
        return tr(
            t"help-command-lookup-failed = Unable to find command `{name}`. Use /help to get "
            t"a list of available commands."
        ).resolve()

    @override
    async def send_error_message(self, error: str, /) -> None:  # type: ignore  # overriding a sync method
        # TODO: error can be a custom Error too
        await send_to(self.get_destination())(render_payload([error_node(tr(t"help-error = Error."), error)]))


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
