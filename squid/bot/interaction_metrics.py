"""Sample acknowledgement latency and event loop scheduling delays.

Acknowledgement times are observed upper bounds, with 50 ms polling resolution
when the loop is healthy. They measure successful responses, not HTTP attempts.
"""

import logging
import time

import anyio
import discord

from squid.observability import add_counter, record_histogram

logger = logging.getLogger(__name__)
POLL_SECONDS = 0.05
ACK_DEADLINE_SECONDS = 3.0


class InteractionMetrics:
    """Keep interactions only until acknowledgement or the initial response deadline."""

    def __init__(self) -> None:
        self._pending: dict[int, discord.Interaction] = {}

    def observe(self, interaction: discord.Interaction) -> None:
        self._pending[interaction.id] = interaction

    def sample(self) -> None:
        now = discord.utils.utcnow()
        for key, interaction in tuple(self._pending.items()):
            age = max(0.0, (now - interaction.created_at).total_seconds())
            acknowledged = interaction.response.is_done()
            if not acknowledged and age < ACK_DEADLINE_SECONDS:
                continue
            del self._pending[key]
            command = interaction.command
            attributes = {
                "squid.interaction.type": interaction.type.name,
                "squid.command.name": command.qualified_name if command is not None else "component",
            }
            if acknowledged:
                record_histogram("squid.interaction.ack_observed_seconds", age, attributes=attributes)
            else:
                add_counter("squid.interaction.ack_deadline_missed", attributes=attributes)
                logger.warning("Interaction unacknowledged at %.3fs", age, extra=attributes)
            if acknowledged and age >= 2.0:
                logger.warning("Interaction acknowledgement observed at %.3fs", age, extra=attributes)

    async def run(self) -> None:
        """Run under the bot's background supervisor for the gateway lifetime."""
        try:
            while True:
                expected = time.monotonic() + POLL_SECONDS
                await anyio.sleep(POLL_SECONDS)
                lag = max(0.0, time.monotonic() - expected)
                record_histogram("squid.discord.event_loop_lag_seconds", lag)
                if lag >= 0.5:
                    logger.warning("Discord event loop delayed by %.3fs", lag)
                self.sample()
        finally:
            self._pending.clear()
