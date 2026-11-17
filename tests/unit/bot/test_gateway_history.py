"""Gateway history stays bounded while retaining process-wide event counts."""

from unittest.mock import patch

from squid.bot.app import RedstoneSquid
from squid.bot.diagnostics.gateway import GatewayHistory
from tests.support.services import bot_services


def test_history_retains_recent_events_and_lifetime_counts() -> None:
    history = GatewayHistory()
    for _ in range(120):
        history.record("disconnect")
    history.record("identify", shard_id=2)
    assert len(history.events) == 100
    assert history.counts == {"disconnect": 120, "identify": 1}
    assert history.events[-1].shard_id == 2
    assert history.events[-1].occurred_at.tzinfo is not None


async def test_gateway_callbacks_and_default_mentions() -> None:
    bot = RedstoneSquid(bot_services())
    assert bot.allowed_mentions is not None
    assert bot.allowed_mentions.to_dict() == {"parse": []}
    with patch("discord.Client.before_identify_hook") as identify:
        await bot.before_identify_hook(0, initial=True)
        identify.assert_awaited_once_with(0, initial=True)
    await bot.on_connect()
    await bot.on_ready()
    await bot.on_disconnect()
    await bot.on_resumed()
    assert [event.kind for event in bot.gateway_history.events] == [
        "identify",
        "connect",
        "ready",
        "disconnect",
        "resume",
    ]
    await bot.close()
