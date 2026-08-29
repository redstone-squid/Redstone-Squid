"""The public command browser and information surface."""

from types import SimpleNamespace
from typing import Any, cast

from discord import app_commands

import squid_ui as sl
from squid.bot.help import REGULATIONS_URL, SUBMISSION_FORM_URL, HelpScreen
from squid_ui.testing import labels, texts, walk


@app_commands.command()
async def example(_interaction: Any) -> None:
    """Explain the example command."""


def _client() -> Any:
    return SimpleNamespace(source_code_url="https://example.invalid/project", user=SimpleNamespace(id=42))


def test_help_screen_owns_one_public_session() -> None:
    assert HelpScreen.session_name == "help"
    assert HelpScreen.timeout == 300
    assert HelpScreen.visibility == "public"


def test_help_screen_uses_a_browser_for_the_command_directory() -> None:
    screen = HelpScreen(_client(), [cast(Any, example)], None)

    nodes = screen.render()

    assert screen._browser is not None
    assert "Close" in labels(nodes)
    links = {node.key: node.url for node in walk(nodes) if isinstance(node, sl.semantic.Link)}
    assert links["invite"].startswith("https://discordapp.com/oauth2/authorize?client_id=42")
    assert links["form"] == SUBMISSION_FORM_URL
    assert links["regulations"] == REGULATIONS_URL


def test_help_screen_renders_focused_and_missing_commands() -> None:
    focused = HelpScreen(_client(), [cast(Any, example)], "example").render()
    missing = HelpScreen(_client(), [cast(Any, example)], "missing").render()

    assert "/example" in texts(focused)
    assert any("No command named `missing`" in text for text in texts(missing))
