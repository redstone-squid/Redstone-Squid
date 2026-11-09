"""Load all cogs together to detect command-name collisions across extensions.

Per-cog taxonomy inspection cannot detect names claimed by two different cogs.
"""

import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

import discord
import pytest_asyncio
from discord.ext import commands

import squid_ui_discord as sd
from squid.bot.app import DEVELOPMENT_EXTENSIONS, EXTENSIONS, CatboxClient, MediaPreviewClient, PostReconciler
from squid.bot.errors import SquidCommandTree
from squid.bot.help import DIRECTORY_CATEGORIES
from squid.bot.reactions import ReactionRouter
from squid.bot.utils.permissions import AccountIdCache
from squid.config import BuildConfig, CommunityConfig
from squid.voting.application import VoteService
from squid.voting.application.ports import VoteActorResolver
from squid_reactivity import LocalTopicBus
from tests.support.runtime import SchedulingRecorder
from tests.support.services import bot_services, unconfigured

LOADABLE = (*EXTENSIONS, *(name for name in DEVELOPMENT_EXTENSIONS if name.startswith("squid.")))
"""Every extension this repo owns, development-only ones included.

A cog that only loads in development mode can still collide with a production one, and would
do so on the developer's machine rather than in CI. `jishaku` is left out because it is third
party and need not be installed for these tests to mean anything.
"""


class RegistrationVotes(VoteService):
    def __init__(self) -> None:
        self.resolver: VoteActorResolver | None = None

    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        self.resolver = resolver


class StubBot(commands.Bot):
    """A real command registry with checked service placeholders and a reaction router."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.none(),
            tree_cls=SquidCommandTree,
        )
        self.topic_bus = LocalTopicBus()
        self.ui = sd.install(self, sd.Config(bus=self.topic_bus))
        self.sessions = self.ui.sessions
        self.services = bot_services(votes=RegistrationVotes())
        self.background_tasks = SchedulingRecorder()
        self.reactions = ReactionRouter(cast(Any, self), self.background_tasks)
        self.post_reconciler = unconfigured(PostReconciler)
        self.account_ids = AccountIdCache()
        self.build_config = BuildConfig()
        self.community_config = CommunityConfig()
        self.catbox = unconfigured(CatboxClient)
        self.media_previews = unconfigured(MediaPreviewClient)
        self.development_mode = False
        self.owner_server_id = 1
        self.bot_name = "stub"
        self.bot_version = "0"
        self.source_code_url = "https://example.invalid"
        self.notification_site_url = None
        self.inference_model = "stub"
        self.inference_reasoning_effort = "low"


@asynccontextmanager
async def _loaded_bot(extensions: Sequence[str]) -> AsyncIterator[commands.Bot]:
    """Load the requested extensions, restoring imported modules when the context exits.

    `load_extension` builds a *fresh* module object and assigns it into `sys.modules`, rather than
    reusing the already-imported one. Without the restore, every other test in the session that had
    imported a name from one of these modules keeps a function whose globals belong to the old
    module object -- so `mock.patch` on the module path silently patches something that function
    can no longer see, and the real implementation runs instead.
    """
    before = dict(sys.modules)
    bot = StubBot()
    try:
        for extension in extensions:
            await bot.load_extension(extension)
            if extension == "squid.bot.reactions":
                # Extension loading replaces this module, including its handler marker types.
                bot.reactions = sys.modules[extension].ReactionRouter(bot, bot.background_tasks)
        yield bot
    finally:
        await bot.ui.close()
        for name, module in before.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def loaded_bot() -> AsyncIterator[commands.Bot]:
    """Load production and development extensions together to check for collisions."""
    async with _loaded_bot(LOADABLE) as bot:
        yield bot


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def production_bot() -> AsyncIterator[commands.Bot]:
    """Load only the extensions registered by a production bot."""
    async with _loaded_bot(EXTENSIONS) as bot:
        yield bot


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
    """The hand-maintained help directory must resolve names across both command trees.

    App-only commands such as polls require checking the application-command tree as well.
    """
    prefix_names = {command.name for command in loaded_bot.commands}
    app_names = {command.name for command in loaded_bot.tree.get_commands(type=discord.AppCommandType.chat_input)}
    listed = {name for _title, names in DIRECTORY_CATEGORIES for name in names}

    assert listed <= prefix_names | app_names
    assert "poll" not in prefix_names, "the directory would find `poll` anyway if it were not app-only"
