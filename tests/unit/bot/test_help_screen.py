"""The public command browser and information surface."""

from dataclasses import dataclass
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.help import REGULATIONS_URL, SUBMISSION_FORM_URL, HelpCog, HelpScreen
from squid_ui.testing import labels, texts, walk
from squid_ui_discord import SessionSpec
from squid_ui_discord.testing import ContextHarness, InteractionHarness


@app_commands.command()
async def example(_interaction: Any) -> None:
    """Explain the example command."""


@dataclass(frozen=True, slots=True)
class User:
    id: int = 42


@dataclass(frozen=True, slots=True)
class Client:
    source_code_url: str = "https://example.invalid/project"
    user: User = User()


def _client() -> Any:
    return cast(Any, Client())


def test_help_screen_owns_one_public_session() -> None:
    assert isinstance(HelpScreen.__response_spec__.session, SessionSpec)
    assert HelpScreen.__response_spec__.session.name == "help"
    assert HelpScreen.__response_spec__.timeout == 300
    assert HelpScreen.__response_spec__.audience == "public"


def test_help_screen_uses_a_browser_for_the_command_directory() -> None:
    screen = HelpScreen(_client(), [cast(Any, example)], None)

    nodes = screen.render()

    assert screen._browser is not None
    assert "Close" not in labels(nodes)
    links = {node.key: node.url for node in walk(nodes) if isinstance(node, sl.semantic.Link)}
    assert links["invite"].startswith("https://discordapp.com/oauth2/authorize?client_id=42")
    assert links["form"] == SUBMISSION_FORM_URL
    assert links["regulations"] == REGULATIONS_URL


def test_help_screen_renders_focused_and_missing_commands() -> None:
    focused = HelpScreen(_client(), [cast(Any, example)], "example").render()
    missing = HelpScreen(_client(), [cast(Any, example)], "missing").render()

    assert "/example" in texts(focused)
    assert any("No command named `missing`" in text for text in texts(missing))


@pytest.mark.parametrize("entrypoint", ["prefix", "slash", "internal"])
@pytest.mark.parametrize("query", [None, "build", "build show"])
async def test_help_entrypoints_share_the_screen(entrypoint: str, query: str | None) -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cast(Any, bot).source_code_url = None
    runtime = sd.install(bot)
    cog = HelpCog(cast(Any, bot))

    class Builds(sd.Cog[commands.Bot]):
        @commands.group(aliases=["b"])
        async def build(self, ctx: commands.Context) -> None:
            """Work with builds."""

        @build.command()
        async def show(self, ctx: commands.Context) -> None:
            """Show a build."""

    builds = Builds(bot)
    await bot.add_cog(builds)
    await bot.add_cog(cog)
    try:
        if entrypoint == "slash":
            interaction = InteractionHarness(client=bot)
            await cast(Any, cog.help).callback(cog, interaction.source, query)
        else:
            context = ContextHarness(bot=bot)
            context.command = builds.build if entrypoint == "internal" else bot.get_command("help")
            if entrypoint == "internal":
                await commands.Context.send_help(context.source, *(() if query is None else (query,)))
            else:
                assert bot.help_command is not None
                help_command = bot.help_command.copy()
                help_command.context = context.source
                await help_command.command_callback(context.source, command=query)
            context.send.assert_awaited_once()

        (session,) = runtime.sessions.active()
        (root,) = session.message_roots
        screen = root.component
        assert isinstance(screen, HelpScreen)
        assert screen.owner is (builds if entrypoint == "internal" else cog)
        if query is None:
            window = await screen._browser.source.fetch(sl.sources.ORIGIN, 100)
            assert {item.qualified_name for item in window.items} == {"build", "help"}
        else:
            assert f"/{query}" in texts(screen.render())
    finally:
        await runtime.close()
        await bot.close()
