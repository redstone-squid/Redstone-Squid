"""Keeping one process's permission cache in step with the others.

Three processes hold three caches, and a grant issued in the API has to become
visible in the bot. Both routes are here: a `LISTEN` connection woken by the
trigger's `NOTIFY`, and a poll that runs regardless. The poll is the durable one,
because a notification delivered while a process was restarting is simply gone.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.permissions.application.cache import SubjectRuleCache
from squid.permissions.application.ports import PermissionStore

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
"""How often the epoch is polled when no notification arrives."""


class WakeListener(Protocol):
    """A source of wake hints, satisfied by `PostgresWakeListener`.

    `run` returns only when the task awaiting it is cancelled.
    """

    async def run(self, on_wake: Callable[[], Awaitable[None]]) -> None:
        """Await *on_wake* on every hint until cancelled, retrying rather than propagating errors.

        Callers also poll, so a lost hint costs latency and not correctness.
        """
        ...


class PermissionEpochWatcher:
    """Clear the local rule cache whenever any process writes a permission."""

    def __init__(
        self,
        store: PermissionStore,
        cache: SubjectRuleCache,
        *,
        listener: WakeListener | None = None,
    ) -> None:
        self._store = store
        self._cache = cache
        self._listener = listener

    @property
    def listener(self) -> WakeListener | None:
        """The wake-hint source, when this deployment configured one."""
        return self._listener

    async def refresh(self) -> None:
        """Read the epoch and invalidate the cache if it moved."""
        if self._cache.observe_epoch(await self._store.epoch()):
            logger.debug("Permission epoch advanced to %d; rule cache cleared", self._cache.epoch)

    async def listen(self) -> None:
        """Follow the notification channel, refreshing on every hint, until cancelled.

        Raises:
            InvalidStateError: this watcher was built without a listener.
        """
        if self._listener is None:
            msg = tr(t"This watcher was built without a wake listener.")
            raise InvalidStateError(msg)
        await self._listener.run(self.refresh)
