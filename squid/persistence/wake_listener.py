"""Let database notifications wake a worker before its next scheduled poll.

Workers find pending work by querying durable database state. LISTEN notifications
only tell them to check sooner; the notification payload is not the work itself.
Callers must keep polling even when this listener is running, since notifications
can be missed while a connection or process is down.
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
        """Call `on_wake` after connecting and when notifications arrive.

        Notifications that arrive together may result in a single callback. Reconnect
        after connection or callback failures, waiting `reconnect_seconds` between
        attempts. Cancellation stops the loop.
        """
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
                        # The same backoff the failure path takes. A server that
                        # accepts connections and drops them at once -- a restart
                        # or a pgbouncer failover -- would otherwise put this
                        # loop into a connect storm.
                        await asyncio.sleep(self._reconnect_seconds)
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
