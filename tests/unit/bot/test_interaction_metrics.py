from datetime import timedelta
from unittest.mock import Mock, patch

import discord

from squid.bot.interaction_metrics import InteractionMetrics


def test_acknowledged_interaction_is_recorded_once() -> None:
    monitor = InteractionMetrics()
    interaction = Mock(id=1, command=None, type=discord.InteractionType.component)
    interaction.created_at = discord.utils.utcnow() - timedelta(seconds=0.4)
    interaction.response.is_done.return_value = True
    monitor.observe(interaction)
    with patch("squid.bot.interaction_metrics.record_histogram") as histogram:
        monitor.sample()
        monitor.sample()
    histogram.assert_called_once()
    assert histogram.call_args.args[1] >= 0.4


def test_pending_interaction_is_retained_until_deadline() -> None:
    monitor = InteractionMetrics()
    interaction = Mock(id=1, command=None, type=discord.InteractionType.component)
    interaction.created_at = discord.utils.utcnow()
    interaction.response.is_done.return_value = False
    monitor.observe(interaction)
    with patch("squid.bot.interaction_metrics.add_counter") as counter:
        monitor.sample()
        counter.assert_not_called()
        interaction.created_at -= timedelta(seconds=4)
        monitor.sample()
        monitor.sample()
    counter.assert_called_once()
