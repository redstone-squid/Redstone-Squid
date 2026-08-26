"""The host supplies development policy and its session registry to library devtools."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from squid.bot.devtools import _authorized, setup
from squid_discord import MountScheduler, SessionRegistry
from squid_discord.devtools import DevTools
from squid_layouts.profiling import MemoryProfiler


async def test_setup_adds_the_generic_cog_with_the_host_registry() -> None:
    registry = SessionRegistry()
    profiler = MemoryProfiler()
    scheduler = MountScheduler(profiler=profiler)
    bot = SimpleNamespace(mounts=registry, layout_scheduler=scheduler, add_cog=AsyncMock())

    await setup(cast(Any, bot))

    cog = bot.add_cog.await_args.args[0]
    assert isinstance(cog, DevTools)
    assert cog._registry is registry
    assert cog._scheduler is scheduler
    assert cog._profiler is profiler


async def test_host_gate_requires_development_mode_and_ownership() -> None:
    bot = SimpleNamespace(development_mode=False, is_owner=AsyncMock(return_value=True))
    ctx = SimpleNamespace(bot=bot, author=object())

    assert not await _authorized(cast(Any, ctx))
    bot.is_owner.assert_not_awaited()

    bot.development_mode = True
    assert await _authorized(cast(Any, ctx))
