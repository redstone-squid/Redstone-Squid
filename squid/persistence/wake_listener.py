"""Optional PostgreSQL LISTEN wake hints for durable pollers.

A notification is a latency hint and nothing more. Every consumer of this class
also polls, so a dropped notification, a dead connection or a process that was
not running at commit time costs latency rather than correctness.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import cast

import asyncpg
from pydantic import SecretStr
from sqlalchemy import make_url

logger = logging.getLogger(__name__)


class PostgresWakeListener:
    """Own one direct PostgreSQL connection used only for wake hints on a channel."""

    def __init__(self, url: SecretStr, *, channel: str, reconnect_seconds: float = 5) -> None:
        self._url = asyncpg_dsn(url)
        self._channel = channel
        self._reconnect_seconds = reconnect_seconds

    @property
    def channel(self) -> str:
        """The PostgreSQL channel this listener subscribes to."""
        return self._channel

    async def run(self, on_wake: Callable[[], Awaitable[None]]) -> None:
        """Reconnect forever and invoke `on_wake` after each notification."""
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
                await active_connection.add_listener(self._channel, notified)
                active_connection.add_termination_listener(terminated)
                await on_wake()
                while True:
                    await wake.wait()
                    wake.clear()
                    if active_connection.is_closed():
                        logger.warning("PostgreSQL %s listener disconnected; reconnecting", self._channel)
                        break
                    await on_wake()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PostgreSQL %s listener disconnected; polling remains active", self._channel)
                await asyncio.sleep(self._reconnect_seconds)
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()


def asyncpg_dsn(url: SecretStr) -> str:
    """Render a SQLAlchemy database URL as the plain DSN asyncpg connects with."""
    return make_url(url.get_secret_value()).set(drivername="postgresql").render_as_string(hide_password=False)
