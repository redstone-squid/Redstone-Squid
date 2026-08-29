"""Every cog must load onto one bot together.

A command name collision between two cogs is only raised when both are registered on the same
bot. The per-cog taxonomy tests instantiate each cog alone, so they cannot see one: a duplicate
name shipped as a `CommandRegistrationError` at process start, with the bot refusing to boot.
"""

import sys
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest_asyncio
from discord.ext import commands

from squid.bot.app import DEVELOPMENT_EXTENSIONS, EXTENSIONS
from squid.bot.errors import SquidCommandTree
from squid.bot.help import DIRECTORY_CATEGORIES

LOADABLE = (*EXTENSIONS, *(name for name in DEVELOPMENT_EXTENSIONS if name.startswith("squid.")))
"""Every extension this repo owns, development-only ones included.

A cog that only loads in development mode can still collide with a production one, and would
do so on the developer's machine rather than in CI. `jishaku` is left out because it is third
party and need not be installed for these tests to mean anything.
"""


class StubBot(commands.Bot):
    """A real `Bot` with the attributes cog constructors read, and nothing else."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.none(),
            tree_cls=SquidCommandTree,
        )
        self.services = MagicMock()
        self.reactions = MagicMock()
        self.background_tasks = MagicMock()
        self.post_reconciler = MagicMock()
        self.account_ids = MagicMock()
        self.build_config = MagicMock()
        self.community_config = MagicMock()
        self.catbox = MagicMock()
        self.media_previews = MagicMock()
        self.development_mode = False
        self.owner_server_id = 1
        self.bot_name = "stub"
        self.bot_version = "0"
        self.source_code_url = "https://example.invalid"
        self.notification_site_url = None
        self.inference_model = "stub"
        self.inference_reasoning_effort = "low"

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes `Bot` does not define, so a cog reading a bot attribute
        # this stub has not listed gets a mock rather than turning this into a maintenance chore.
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def loaded_bot() -> AsyncIterator[commands.Bot]:
    """Load the real extension list, then put `sys.modules` back exactly as it was.

    `load_extension` builds a *fresh* module object and assigns it into `sys.modules`, rather than
    reusing the already-imported one. Without the restore, every other test in the session that had
    imported a name from one of these modules keeps a function whose globals belong to the old
    module object -- so `mock.patch` on the module path silently patches something that function
    can no longer see, and the real implementation runs instead.
    """
    before = dict(sys.modules)
    bot = StubBot()
    try:
        for extension in LOADABLE:
            await bot.load_extension(extension)
        yield bot
    finally:
        for name, module in before.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module


async def test_every_extension_loads_onto_one_bot(loaded_bot: commands.Bot) -> None:
    assert set(loaded_bot.extensions) == set(LOADABLE)


async def test_no_two_cogs_claim_the_same_command_name(loaded_bot: commands.Bot) -> None:
    """The assertion `load_extension` already makes, restated so a failure names the collision."""
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for command in loaded_bot.walk_commands():
        for name in (
            command.qualified_name,
            *(f"{command.full_parent_name} {alias}".strip() for alias in command.aliases),
        ):
            owner = type(command.cog).__name__ if command.cog is not None else "?"
            if name in seen:
                collisions.append(f"{name!r} claimed by both {seen[name]} and {owner}")
            seen[name] = owner

    assert not collisions, "; ".join(collisions)


async def test_the_error_browser_is_app_only(loaded_bot: commands.Bot) -> None:
    """Diagnostics use one private app-command screen; the deliberate owner failure stays hidden."""
    lookup = loaded_bot.tree.get_command("errors")
    raiser = loaded_bot.get_command("raise-error")

    assert lookup is not None
    assert raiser is not None
    assert lookup.module == "squid.bot.diagnostics"
    assert type(raiser.cog).__name__ == "Admin"
    assert loaded_bot.get_command("e") is raiser
    assert loaded_bot.get_command("error") is None


def test_extension_list_has_no_duplicates() -> None:
    assert len(EXTENSIONS) == len(set(EXTENSIONS))


def test_layout_showcase_is_development_only() -> None:
    assert "squid.bot.layout_showcase" not in EXTENSIONS
    assert "squid.bot.layout_showcase" in DEVELOPMENT_EXTENSIONS


async def test_the_help_directory_names_commands_that_exist(loaded_bot: commands.Bot) -> None:
    """Every category entry has to resolve, in either tree.

    The map is edited by hand and read by name, so a renamed or retired group leaves an
    entry that quietly lists nothing: `patterns` survived phase 2 and `vote` survived phase
    5.1 this way. Both trees are consulted because `/poll` is app-only, and the directory
    listing it at all is the point of `_root_commands`.
    """
    prefix_names = {command.name for command in loaded_bot.commands}
    app_names = {command.name for command in loaded_bot.tree.get_commands(type=discord.AppCommandType.chat_input)}
    listed = {name for _title, names in DIRECTORY_CATEGORIES for name in names}

    assert listed <= prefix_names | app_names
    assert "poll" not in prefix_names, "the directory would find `poll` anyway if it were not app-only"
