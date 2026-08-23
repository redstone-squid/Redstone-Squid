"""Best-effort persistence for a reactive shared namespace."""

import asyncio
import logging
from collections.abc import Callable, Mapping

import anyio

from squid_reactive import Shared, TopicBus, export_state, restore_state
from squid_stores.scoped import ScopedStore, Slot

_logger = logging.getLogger(__name__)


class PersistedPool[ScopeT, SharedT: Shared[ScopeT]]:
    """Hydrate one shared namespace per scope and persist committed changes.

    Loading is explicit and asynchronous because it reads the store. Once a namespace is
    loaded, commits enqueue a snapshot synchronously and a supervised anyio worker performs
    the best-effort write. Store failures are sent to on_error and never travel back
    through the action that produced the state change.
    """

    def __init__(
        self,
        namespace: type[SharedT],
        bus: TopicBus,
        *,
        store: ScopedStore,
        slot: Slot[ScopeT, Mapping[str, object]],
        factory: Callable[[TopicBus, ScopeT], SharedT] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.namespace = namespace
        self.bus = bus
        self.store = store
        self.slot = slot
        self._factory = factory
        self._on_error = on_error
        self._handles: dict[ScopeT, SharedT] = {}
        self._pending: dict[ScopeT, Mapping[str, object]] = {}
        self._load_lock = asyncio.Lock()
        self._pending_event = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task_group: anyio.abc.TaskGroup | None = None
        self._closing = False

    async def load(self, scope: ScopeT) -> SharedT:
        """Return the canonical namespace for scope, hydrating it on the first load."""
        async with self._load_lock:
            existing = self._handles.get(scope)
            if existing is not None:
                return existing
            await self._ensure_worker()
            created = self._make(scope)
            values = await self.store.get(self.slot, scope)
            if values is not None:
                restore_state(created, values)
            created._add_commit_listener(lambda created=created, scope=scope: self._stage(created, scope))
            self._handles[scope] = created
            return created

    async def flush(self) -> None:
        """Wait until every snapshot queued so far has been attempted."""
        if self._task_group is None:
            return
        await self._idle.wait()

    async def close(self) -> None:
        """Drain pending writes and stop the owned persistence worker."""
        async with self._load_lock:
            task_group = self._task_group
            if task_group is None:
                return
            self._closing = True
            self._pending_event.set()
            await task_group.__aexit__(None, None, None)
            self._task_group = None

    def _make(self, scope: ScopeT) -> SharedT:
        created = (self._factory or self.namespace)(self.bus, scope)
        if not isinstance(created, self.namespace):
            msg = f"persisted namespace factory returned {type(created).__name__}, not {self.namespace.__name__}"
            raise TypeError(msg)
        if created.bus is not self.bus:
            msg = "persisted namespace factory returned a namespace on another TopicBus"
            raise TypeError(msg)
        if created.scope != scope:
            msg = "persisted namespace factory returned a namespace for another scope"
            raise TypeError(msg)
        return created

    async def _ensure_worker(self) -> None:
        if self._closing:
            msg = "persisted pool is closed"
            raise RuntimeError(msg)
        if self._task_group is not None:
            return
        task_group = anyio.create_task_group()
        self._task_group = await task_group.__aenter__()
        self._task_group.start_soon(self._drain)

    def _stage(self, namespace: SharedT, scope: ScopeT) -> None:
        try:
            values = dict(export_state(namespace))
        except BaseException as error:
            self._report(error)
            return
        self._pending[scope] = values
        self._idle.clear()
        self._pending_event.set()

    async def _drain(self) -> None:
        while True:
            await self._pending_event.wait()
            self._pending_event.clear()
            while self._pending:
                scope, values = self._pending.popitem()
                try:
                    await self.store.put(self.slot, scope, values)
                except BaseException as error:
                    self._report(error)
            self._idle.set()
            if self._closing:
                return

    def _report(self, error: BaseException) -> None:
        if self._on_error is None:
            _logger.error("persisted shared-state write failed", exc_info=(type(error), error, error.__traceback__))
            return
        try:
            self._on_error(error)
        except BaseException:
            _logger.exception("persisted shared-state error hook failed")


__all__ = ["PersistedPool"]
