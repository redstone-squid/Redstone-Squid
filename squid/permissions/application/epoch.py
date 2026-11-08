"""Refresh the local permission cache from the shared database epoch.

The epoch is a shared counter incremented whenever permissions change. Each
process compares it with its cached epoch to detect rules that need reloading.
The runtime calls `refresh` periodically. An optional PostgreSQL listener also
triggers refreshes for lower latency; polling catches notifications missed
during disconnects or restarts.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.permissions.application.cache import SubjectRuleCache
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
"""How often the epoch is polled when no notification arrives."""


class WakeListener(Protocol):
    """Notify the watcher when it should reread the epoch; implemented by `PostgresWakeListener`."""

    async def run(self, on_wake: Callable[[], Awaitable[None]]) -> None: ...


class PermissionEpochWatcher:
    """Invalidate local rules when a refresh observes a changed permission epoch."""

    def __init__(
        self,
        cache: SubjectRuleCache,
        *,
        uow_factory: UnitOfWorkFactory[PermissionUnitOfWork],
        listener: WakeListener | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._cache = cache
        self._listener = listener

    @property
    def listener(self) -> WakeListener | None:
        """The wake-hint source, when this deployment configured one."""
        return self._listener

    async def refresh(self) -> None:
        """Read the epoch and invalidate the cache if it moved."""
        async with self._uow_factory() as uow:
            epoch = await uow.permissions.epoch()
        if self._cache.observe_epoch(epoch):
            logger.debug("Permission epoch advanced to %d; rule cache cleared", self._cache.epoch)

    async def listen(self) -> None:
        """Follow the notification channel, refreshing on every hint."""
        if self._listener is None:
            msg = tr("This watcher was built without a wake listener.")
            raise InvalidStateError(msg)
        await self._listener.run(self.refresh)
