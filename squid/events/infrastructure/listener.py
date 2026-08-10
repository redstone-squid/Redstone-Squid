"""Optional PostgreSQL LISTEN wake hints for the durable event poller."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import cast

import asyncpg
from pydantic import SecretStr
from sqlalchemy import make_url

logger = logging.getLogger(__name__)
CHANNEL = "squid_domain_events"


class DomainEventWakeListener:
    """Own one direct PostgreSQL connection used only for event wake hints."""

    def __init__(self, url: SecretStr, *, reconnect_seconds: float = 5) -> None:
        self._url = _asyncpg_url(url.get_secret_value())
        self._reconnect_seconds = reconnect_seconds

    async def run(self, process_events: Callable[[], Awaitable[None]]) -> None:
        """Reconnect forever and invoke the durable poller after each notification."""
        while True:
            connection: asyncpg.Connection | None = None
            wake = asyncio.Event()

            def notified(
                _connection: asyncpg.Connection,
                _process_id: int,
                _channel: str,
                _payload: str,
                _wake: asyncio.Event = wake,
            ) -> None:
                _wake.set()

            def terminated(_connection: asyncpg.Connection, _wake: asyncio.Event = wake) -> None:
                _wake.set()

            try:
                active_connection = cast(asyncpg.Connection, await asyncpg.connect(self._url))
                connection = active_connection
                await active_connection.add_listener(CHANNEL, notified)
                active_connection.add_termination_listener(terminated)
                await process_events()
                while True:
                    await wake.wait()
                    wake.clear()
                    if active_connection.is_closed():
                        logger.warning("PostgreSQL domain-event listener disconnected; reconnecting")
                        break
                    await process_events()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PostgreSQL domain-event listener disconnected; polling remains active")
                await asyncio.sleep(self._reconnect_seconds)
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()


def _asyncpg_url(raw_url: str) -> str:
    url = make_url(raw_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)
