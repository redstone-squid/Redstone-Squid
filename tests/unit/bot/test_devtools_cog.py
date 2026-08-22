"""The host supplies development policy and its session registry to library devtools."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from squid.bot.devtools import _authorized, setup
from squid_layouts.discord import SessionRegistry
from squid_layouts.discord.devtools import DevTools


async def test_setup_adds_the_generic_cog_with_the_host_registry() -> None:
    registry = SessionRegistry()
    bot = SimpleNamespace(mounts=registry, add_cog=AsyncMock())

    await setup(cast(Any, bot))

    cog = bot.add_cog.await_args.args[0]
    assert isinstance(cog, DevTools)
    assert cog._registry is registry


async def test_host_gate_requires_development_mode_and_ownership() -> None:
    bot = SimpleNamespace(development_mode=False, is_owner=AsyncMock(return_value=True))
    ctx = SimpleNamespace(bot=bot, author=object())

    assert not await _authorized(cast(Any, ctx))
    bot.is_owner.assert_not_awaited()

    bot.development_mode = True
    assert await _authorized(cast(Any, ctx))
