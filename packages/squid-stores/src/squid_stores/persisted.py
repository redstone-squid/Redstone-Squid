"""Best-effort persistence for a reactive shared namespace."""

import asyncio
import logging
from collections.abc import Callable, Hashable, Mapping
from typing import Any

import anyio

from squid_reactive import Shared, SharedPool, TopicBus, export_state, restore_state
from squid_stores.scoped import ScopedStore, Slot

_logger = logging.getLogger(__name__)


class PersistedPool[ScopeT: Hashable, SharedT: Shared[Any]]:
    """Hydrate one shared namespace per scope and persist committed changes.

    Loading is explicit and asynchronous because it reads the store. Once a namespace is
    loaded, commits enqueue a snapshot synchronously and a supervised anyio worker performs
    the best-effort write. Store failures are sent to on_error and never travel back
    through the action that produced the state change.

    The canonical-handle machinery is a :class:`~squid_reactive.pool.SharedPool` held privately.
    This class composes one rather than subclassing it because `SharedPool.get` is synchronous and
    would publish a handle before the store had been read: a concurrent `load` of the same scope
    would then be handed an un-hydrated namespace and hydrate nothing. The pool's `_create` and
    `_adopt` halves exist for exactly this, so hydration can happen between them.
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
        self.store = store
        self.slot = slot
        self._pool: SharedPool[ScopeT, SharedT] = SharedPool(namespace, bus, factory=factory or namespace)
        self._on_error = on_error
        self._listeners: dict[ScopeT, Callable[[], None]] = {}
        self._pending: dict[ScopeT, Mapping[str, object]] = {}
        self._load_lock = asyncio.Lock()
        self._pending_event = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task_group: anyio.abc.TaskGroup | None = None
        self._closing = False

    @property
    def namespace(self) -> type[SharedT]:
        """The one shared namespace class this pool owns."""
        return self._pool.namespace

    @property
    def bus(self) -> TopicBus:
        """The topic bus every namespace this pool retains must hold."""
        return self._pool.bus

    async def load(self, scope: ScopeT) -> SharedT:
        """Return the canonical namespace for scope, hydrating it on the first load."""
        async with self._load_lock:
            existing = self._pool.get_existing(scope)
            if existing is not None:
                return existing
            await self._ensure_worker()
            created = self._pool._create(scope)
            values = await self.store.get(self.slot, scope)
            if values is not None:
                restore_state(created, values)
            canonical = self._pool._adopt(scope, created)
            if canonical is created:
                listener = self._listener_for(created, scope)
                self._listeners[scope] = listener
                created._add_commit_listener(listener)
            return canonical

    def get_existing(self, scope: ScopeT) -> SharedT | None:
        """Return the namespace already loaded for scope, or None. Reads nothing."""
        return self._pool.get_existing(scope)

    def active(self) -> Mapping[ScopeT, SharedT]:
        """Snapshot the loaded namespaces. Inspection only, so it stays synchronous."""
        return self._pool.active()

    async def drop(self, scope: ScopeT) -> SharedT | None:
        """Retire scope and stop persisting through the handle that owned it.

        The retired namespace stays usable, reactive and readable; it simply stops writing to a
        slot it no longer owns. Detaching is not optional: two generations sharing one slot would
        race for the row, and retired state would resurrect on the next load. Anything this handle
        already staged still gets written -- a drop retires a lifetime, it does not undo a
        committed action -- which is why this drains before returning.
        """
        async with self._load_lock:
            retired = self._pool.drop(scope)
            if retired is None:
                return None
            self._detach(retired, scope)
        await self.flush()
        return retired

    async def clear(self) -> None:
        """Retire every scope on the same terms as `drop`."""
        async with self._load_lock:
            for scope, handle in self._pool.active().items():
                self._detach(handle, scope)
            self._pool.clear()
        await self.flush()

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
                self._closing = True
                return
            self._closing = True
            for scope, handle in self._pool.active().items():
                self._detach(handle, scope)
            self._pending_event.set()
            await task_group.__aexit__(None, None, None)
            self._task_group = None

    def _listener_for(self, namespace: SharedT, scope: ScopeT) -> Callable[[], None]:
        def stage() -> None:
            self._stage(namespace, scope)

        return stage

    def _detach(self, namespace: SharedT, scope: ScopeT) -> None:
        listener = self._listeners.pop(scope, None)
        if listener is not None:
            namespace._remove_commit_listener(listener)

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
