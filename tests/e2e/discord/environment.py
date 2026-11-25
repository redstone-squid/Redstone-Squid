from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from typing import override
from unittest.mock import create_autospec

import discord
import pytest
import simcord
from discord.ext import commands

from squid.bot.app import RedstoneSquid
from squid.bot.extensions import Extension, ExtensionManager
from squid.core.permissions.application.services import PermissionService
from squid.core.settings.application import SettingsService
from squid.runtime import BotServices
from tests.support.services import bot_services, is_configured
from tests.support.settings import FakeSettingsUnitOfWork, settings_state


class CommandTestBot(RedstoneSquid):
    """Load only the extensions under test during the simulated Discord login."""

    @override
    async def setup_hook(self) -> None:
        await self.extension_manager.start(
            partial(commands.Bot.load_extension, self), partial(commands.Bot.unload_extension, self)
        )


@asynccontextmanager
async def command_environment(
    *extensions: Extension, services: BotServices | None = None, development_mode: bool = False
) -> AsyncIterator[tuple[RedstoneSquid, simcord.Env]]:
    services = services or bot_services()
    if not is_configured(services.settings):
        settings = SettingsService(
            permissions=create_autospec(PermissionService, instance=True),
            uow_factory=partial(FakeSettingsUnitOfWork, settings_state()),
        )
        services = replace(services, settings=settings)
    bot = CommandTestBot(services, development_mode=development_mode)
    bot.extension_manager = ExtensionManager(extensions)
    async with bot.background_tasks.running(), bot.ui.running(), simcord.run(bot) as env:
        try:
            await bot.tree.sync()
            env.create_guild()
            yield bot, env
        finally:
            await bot.close()


def allow_idle_reaction_workers(bot: RedstoneSquid, env: simcord.Env, monkeypatch: pytest.MonkeyPatch) -> None:
    # The workers persist after each reaction. Only the queue read waits for external input.
    async def read_next[T](get: Callable[[], Awaitable[T]]) -> T:
        return await env.external_wait(get(), reason="next Discord reaction")

    for queue in bot.reactions._queues:
        monkeypatch.setattr(queue, "get", partial(read_next, queue.get))


def displayed_text(message: discord.Message) -> list[str]:
    view = discord.ui.LayoutView.from_message(message)
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
