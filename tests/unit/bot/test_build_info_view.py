"""Build information session lifetime tests."""

from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from squid.bot.submission.ui.views import BUILD_INFO_TIMEOUT_SECONDS, BuildInfoView
from squid.builds.domain import Build


async def test_build_info_view_expires_and_disables_every_control() -> None:
    """Stateful build navigation does not remain registered indefinitely."""
    view = BuildInfoView[Any](Build(id=42))
    message = AsyncMock(spec=discord.Message)
    view._message = cast(discord.Message, message)  # pyright: ignore[reportPrivateUsage]

    await view.on_timeout()

    assert view.timeout == BUILD_INFO_TIMEOUT_SECONDS
    components = [child.item if isinstance(child, discord.ui.DynamicItem) else child for child in view.walk_children()]
    controls = [component for component in components if isinstance(component, discord.ui.Button | discord.ui.Select)]
    assert controls
    assert all(control.disabled for control in controls)
    message.edit.assert_awaited_once()
    assert message.edit.await_args.kwargs["view"] is view
    assert message.edit.await_args.kwargs["allowed_mentions"].everyone is False
