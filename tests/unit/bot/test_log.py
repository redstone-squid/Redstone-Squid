import logging
from unittest.mock import Mock

import pytest

from squid.bot.log import LoggingCog


@pytest.mark.asyncio
async def test_log_uses_logging_without_discord_io(caplog: pytest.LogCaptureFixture) -> None:
    bot = Mock(owner_id=None)
    cog = LoggingCog(bot)

    with caplog.at_level(logging.INFO, logger="squid.bot.log"):
        await cog.log("Bot started")

    assert "Bot started" in caplog.messages[0]


@pytest.mark.asyncio
async def test_command_log_excludes_arguments_and_raw_content(caplog: pytest.LogCaptureFixture) -> None:
    bot = Mock()
    cog = LoggingCog(bot)
    ctx = Mock()
    ctx.command.qualified_name = "build submit"
    ctx.args = (object(), ctx, "super-secret-argument")
    ctx.kwargs = {"notes": "private-content"}
    ctx.guild.id = 456
    ctx.author.id = 789
    ctx.interaction = None

    with caplog.at_level(logging.INFO, logger="squid.bot.log"):
        await cog.log_command_usage(ctx)

    record = caplog.records[0]
    assert record.message == "Discord command invoked"
    assert record.__dict__["squid.command.name"] == "build submit"
    assert record.__dict__["squid.guild.id"] == 456
    assert "super-secret-argument" not in caplog.text
    assert "private-content" not in caplog.text
