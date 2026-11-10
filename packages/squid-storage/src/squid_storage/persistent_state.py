"""Restore shared view state on load and save snapshots after reactive commits.

PersistentStatePool wraps ReactivePool with asynchronous storage. The first
load creates an instance, restores its saved fields, and only then makes it
available to other consumers. Later commits queue snapshots for a worker, so
storage latency does not hold up the reactive action.
"""

import asyncio
import logging
from collections.abc import Callable, Hashable, Mapping

import anyio
from anyio.abc import TaskStatus

from squid_reactivity import Reactive, ReactivePool, export_state, restore_state
from squid_storage.scoped import ScopedStore, Slot

_logger = logging.getLogger(__name__)


class PersistentStatePool[ScopeT: Hashable, SharedT: Reactive]:
    """Hydrate one reactive model per scope and persist its committed state.

    Start run in a host-owned task group before the first load. Hydration completes
    before a handle enters the cache, so concurrent loads share fully restored state.

    Commits synchronously stage snapshots for an asynchronous worker. Pending writes
    coalesce by scope: only the newest queued snapshot is kept, with no ordering across
    scopes. Failed writes are reported to on_error, or logged, without retrying or
    rolling back the committed action. Store reads during load still propagate errors.
    Close drains the queue and permanently stops accepting loads and new writes.
    """

    def __init__(
        self,
        factory: Callable[[ScopeT], SharedT],
        *,
        store: ScopedStore,
        slot: Slot[ScopeT, Mapping[str, object]],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.store = store
        self.slot = slot
        self._pool: ReactivePool[ScopeT, SharedT] = ReactivePool(factory)
        self._on_error = on_error
        self._listeners: dict[ScopeT, Callable[[], None]] = {}
        self._pending: dict[ScopeT, Mapping[str, object]] = {}
        self._load_lock = asyncio.Lock()
        self._pending_event = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._running = False
        self._closing = False

    async def run(self, *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
        """Own the persistence worker until close drains it or the caller cancels.

        Use await tasks.start(pool.run) to wait until loading can begin. This coroutine
        owns both entry and exit of the task group, even when requests load or close the
        pool from other tasks.
        """
        if self._running:
            msg = "persisted pool is already running"
            raise RuntimeError(msg)
        if self._closing:
            msg = "persisted pool is closed"
            raise RuntimeError(msg)
        self._running = True
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self._drain)
                task_status.started()
        finally:
            self._running = False

    async def load(self, scope: ScopeT) -> SharedT:
        """Return the canonical model for scope, hydrating it on the first load."""
        async with self._load_lock:
            # Checked before the cache: `close` is terminal for the pool, even for a scope it
            # already holds. A caller still holding that namespace keeps an ordinary `Reactive`;
            # what ended is the pool's willingness to hand out more and to persist them.
            if self._closing:
                msg = "persisted pool is closed"
                raise RuntimeError(msg)
            existing = self._pool.get_existing(scope)
            if existing is not None:
                return existing
            if not self._running:
                msg = "persisted pool is not running; start run() before loading a model"
                raise RuntimeError(msg)
            created = self._pool._create(scope)
            # Restore before adoption: the synchronous pool's normal get() would
            # expose the instance before its persisted fields had been loaded.
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
        """Return the model already loaded for scope, or None. Reads nothing."""
        return self._pool.get_existing(scope)

    def active(self) -> Mapping[ScopeT, SharedT]:
        """Snapshot the loaded models. Inspection only, so it stays synchronous."""
        return self._pool.active()

    async def delete(self, scope: ScopeT) -> SharedT | None:
        """Retire a cached handle and detach its persistence listener, preserving stored data.

        Wait for queued writes to be attempted before returning. A later load restores the
        stored value into a new handle. The retired handle remains reactive, but its future
        commits cannot overwrite the new handle's slot. Return None if the scope was not loaded.
        """
        async with self._load_lock:
            retired = self._pool.delete(scope)
            if retired is None:
                return None
            self._detach(retired, scope)
        await self.flush()
        return retired

    async def clear(self) -> None:
        """Retire every cached handle as delete does, preserving the stored values."""
        async with self._load_lock:
            for scope, handle in self._pool.active().items():
                self._detach(handle, scope)
            self._pool.clear()
        await self.flush()

    async def flush(self) -> None:
        """Wait until the worker is idle, including any writes queued while waiting.

        Coalesced snapshots are not written individually, and failed writes do not make
        flush raise. Return immediately if the worker is not running.
        """
        if not self._running:
            return
        await self._idle.wait()

    async def close(self) -> None:
        """Drain pending writes and stop accepting loads; `run` returns once drained."""
        # Listeners stay attached deliberately. Detaching here would block the write but also
        # silence it; `_stage`'s closed guard blocks it and reports it, which is what a host
        # that outlived its pool needs to hear.
        self._closing = True
        self._pending_event.set()
        if self._running:
            await self._idle.wait()

    def _listener_for(self, namespace: SharedT, scope: ScopeT) -> Callable[[], None]:
        def stage() -> None:
            self._stage(namespace, scope)

        return stage

    def _detach(self, namespace: SharedT, scope: ScopeT) -> None:
        listener = self._listeners.pop(scope, None)
        if listener is not None:
            namespace._remove_commit_listener(listener)

    def _stage(self, namespace: SharedT, scope: ScopeT) -> None:
        if self._closing:
            # Queuing here would drop the write silently: no worker is left to drain it.
            # A namespace outliving its pool is the host's mistake, so it is reported like
            # any other persistence failure rather than raised into the action.
            self._report(RuntimeError(f"persisted pool is closed; dropped a write for scope {scope!r}"))
            return
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


__all__ = ["PersistentStatePool"]
