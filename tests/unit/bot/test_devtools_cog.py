"""The host supplies development policy and its session manager to library devtools."""

from dataclasses import dataclass, field
from typing import Any, cast

from squid.bot.devtools import _authorized, setup
from squid_ui.profiling import MemoryProfiler
from squid_ui_discord import MessageRootScheduler, SessionManager
from squid_ui_discord.devtools import DevTools
from squid_ui_discord.testing import AsyncCallRecorder


@dataclass(frozen=True)
class ClientRuntime:
    scheduler: MessageRootScheduler


@dataclass
class Bot:
    sessions: SessionManager | None = None
    client_runtime: ClientRuntime | None = None
    development_mode: bool = False
    add_cog: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)
    is_owner: AsyncCallRecorder = field(default_factory=lambda: AsyncCallRecorder(result=True))


@dataclass(frozen=True)
class Context:
    bot: Bot
    author: object


async def test_setup_adds_the_generic_cog_with_the_host_manager() -> None:
    manager = SessionManager()
    profiler = MemoryProfiler()
    scheduler = MessageRootScheduler(profiler=profiler)
    bot = Bot(sessions=manager, client_runtime=ClientRuntime(scheduler))

    await setup(cast(Any, bot))

    cog = bot.add_cog.await_args.args[0]
    assert isinstance(cog, DevTools)
    assert cog._manager is manager
    assert cog._scheduler is scheduler
    assert cog._profiler is profiler


async def test_host_gate_requires_development_mode_and_ownership() -> None:
    bot = Bot()
    ctx = Context(bot=bot, author=object())

    assert not await _authorized(cast(Any, ctx))
    bot.is_owner.assert_not_awaited()

    bot.development_mode = True
    assert await _authorized(cast(Any, ctx))
