import logging
from unittest.mock import AsyncMock, Mock

import pytest

from squid.bot.log import LoggingCog


@pytest.mark.asyncio
async def test_log_fetches_uncached_owner_and_uses_logging(caplog: pytest.LogCaptureFixture) -> None:
    owner = Mock(send=AsyncMock())
    bot = Mock(owner_id=123, get_user=Mock(return_value=None), fetch_user=AsyncMock(return_value=owner))
    cog = LoggingCog(bot)

    with caplog.at_level(logging.INFO, logger="squid.bot.log"):
        await cog.log("Bot started")

    bot.fetch_user.assert_awaited_once_with(123)
    owner.send.assert_awaited_once()
    assert "Bot started" in caplog.messages[0]


@pytest.mark.asyncio
async def test_log_uses_cached_owner() -> None:
    owner = Mock(send=AsyncMock())
    bot = Mock(owner_id=123, get_user=Mock(return_value=owner), fetch_user=AsyncMock())
    cog = LoggingCog(bot)

    await cog.log("Bot started")

    bot.fetch_user.assert_not_awaited()
    owner.send.assert_awaited_once()
