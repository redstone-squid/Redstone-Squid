"""Render command discovery, focused command help, and project links."""

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, Self, cast, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.suggestions.application import Candidate, rank
from squid.suggestions.domain import MAX_SUGGESTIONS
from squid_i18n import inline_code, md_tr
from squid_ui.text import TextLike

if TYPE_CHECKING:
    import squid.bot.app

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


def _public_commands(commands_: Iterable[AnyCommand]) -> list[AnyCommand]:
    """Hide internal commands and their descendants without running authorization checks.

    Prefix commands use `hidden`; app-only commands can set `extras={"help_hidden": True}`.
    Supply prefix entries first so hybrid commands retain their prefix visibility metadata.
    Normal commands stay discoverable even when the reader cannot currently run them.
    """
    by_name: dict[str, AnyCommand] = {}
    for command in commands_:
        by_name.setdefault(command.qualified_name, command)

    visible: list[AnyCommand] = []
    for command in by_name.values():
        ancestor: AnyCommand | None = command
        while ancestor is not None:
            ancestor = by_name.get(ancestor.qualified_name, ancestor)
            if getattr(ancestor, "hidden", False) or ancestor.extras.get("help_hidden", False):
                break
            # discord.py annotates prefix parents as GroupMixin, but these are command groups.
            ancestor = cast(AnyCommand | None, ancestor.parent)
        else:
            visible.append(command)
    return visible


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
        self._commands = tuple(_public_commands(commands_))
        self._needle = command
        self._focused = self._find(command) if command is not None else None
        self._browser = sp.Browser(
            sl.sources.list_source(item for item in self._commands if item.parent is None),
            key="commands",
            identity=lambda item: item.qualified_name,
            label=lambda item: f"/{item.qualified_name}",
            summary=lambda item: sl.text.Text(t"{inline_code(f'/{item.qualified_name}')} — {_summary(item)}"),
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
        children = tuple(
            item
            for item in self._commands
            if isinstance(item.parent, Command | app_commands.Group)
            and item.parent.qualified_name == command.qualified_name
        )
        return sl.section(
            sl.heading(heading),
            sl.paragraph(description).truncate(),
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
                sl.paragraph(md_tr(t"help-command-unavailable = No command named `{needle}` is available.")),
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
        help_command = Help()
        help_command.cog = self
        self.bot.help_command = help_command

    # /help [command]
    @sd.command(
        description=tr(t"command-help-help = Show a grouped command directory or focused command details."),
        name=tr(t"command-help-name = help"),
        options={
            "command": sd.Option(
                name=tr(t"command-help-command-name = command"),
                description=tr(t"help-the-command-to-get-help-for = The command to get help for."),
            )
        },
    )
    async def help(self, request: sd.Request[Self, BotT], command: str | None) -> HelpScreen:
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
        """Complete public command names without evaluating caller permissions.

        Not a registry source: the candidates are this process's loaded command tree, which no
        other surface can see. It still ranks through the shared matcher so `/help` orders results
        the same way every other autocomplete does.
        """
        public = _public_commands(self._all_commands())
        if not needle:
            return [
                app_commands.Choice(name=command.qualified_name, value=command.qualified_name)
                for command in public
                if command.parent is None
            ][:MAX_SUGGESTIONS]

        candidates = [Candidate.create(command.qualified_name) for command in public]
        return [
            app_commands.Choice(name=match.label, value=match.value)
            for match in rank(needle, candidates, limit=MAX_SUGGESTIONS)
        ]


class Help(commands.HelpCommand):
    """Adapt prefix and internal help requests to the shared command browser."""

    def __init__(self) -> None:
        super().__init__(command_attrs={"help": "Show help for a command or a group of commands."})

    async def _show(self, command: str | None = None, *, commands_: Sequence[AnyCommand] | None = None) -> None:
        cog = self.cog
        assert isinstance(cog, HelpCog)
        # Internal send_help calls retain the invoking command's UI scope.
        request = await sd.request(cast(commands.Context[commands.Bot], self.context))
        await request.respond(HelpScreen(cog.bot, cog._all_commands() if commands_ is None else commands_, command))

    @override
    async def command_callback(self, ctx: commands.Context, /, *, command: str | None = None) -> None:
        await self.prepare_help_command(ctx, command)
        if command is not None:
            cog = ctx.bot.get_cog(command)
            if cog is not None:
                await self.send_cog_help(cog)
                return
            # Resolve prefix aliases before looking up the shared prefix and slash directory.
            resolved = ctx.bot.get_command(command)
            if resolved is not None:
                command = resolved.qualified_name
        await self._show(command)

    @override
    async def send_bot_help(self, mapping: Mapping[Cog | None, list[Command[Any, ..., Any]]], /) -> None:
        await self._show()

    @override
    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        await self._show(command.qualified_name)

    @override
    async def send_group_help(self, group: Group[Any, ..., Any], /) -> None:
        await self._show(group.qualified_name)

    @override
    async def send_cog_help(self, cog: Cog, /) -> None:
        await self._show(commands_=[*cog.walk_commands(), *cog.walk_app_commands()])


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(HelpCog(bot))
