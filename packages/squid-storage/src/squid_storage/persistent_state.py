"""Restore shared view state on load and save snapshots after reactive commits.

PersistentStatePool retains one model per scope. The first load creates an
instance, restores its saved fields, and only then makes it available to other
consumers. Later commits that change a persistent field queue snapshots for a
worker, so storage latency does not hold up the reactive action.
"""

import asyncio
import logging
from collections.abc import Callable, Hashable, Mapping
from functools import partial
from types import MappingProxyType

import anyio
from anyio.abc import TaskStatus

from squid_reactivity import ChangeListener, Reactive, export_state, on_change, restore_state
from squid_storage.scoped import ScopedStore, Slot

_logger = logging.getLogger(__name__)


class PersistentStatePool[ScopeT: Hashable, SharedT: Reactive]:
    """Hydrate one reactive model per scope and persist its committed state.

    Start run in a host-owned task group before the first load. Hydration completes
    before a handle enters the cache, so concurrent loads share fully restored state.

    A commit that changes a persistent field synchronously stages a snapshot for an
    asynchronous worker. Pending writes coalesce by scope: only the newest queued
    snapshot is kept, with no ordering across scopes. Failed writes are reported to on_error, or logged, without retrying or
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
        self._factory = factory
        self._models: dict[ScopeT, SharedT] = {}
        self._on_error = on_error
        self._listeners: dict[ScopeT, ChangeListener[dict[str, object]]] = {}
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
            existing = self._models.get(scope)
            if existing is not None:
                return existing
            if not self._running:
                msg = "persisted pool is not running; start run() before loading a model"
                raise RuntimeError(msg)
            created = self._factory(scope)
            if not isinstance(created, Reactive):
                msg = "reactive factory must return a Reactive object"
                raise TypeError(msg)
            # Sharing an instance across scopes would restore and save different
            # storage identities through the same mutable state.
            if any(held is created for held in self._models.values()):
                msg = "reactive factory reused one object for different keys"
                raise ValueError(msg)
            values = await self.store.get(self.slot, scope)
            if values is not None:
                restore_state(created, values)
            # The load lock covers construction and hydration. Publish only a fully
            # restored model, and leave failed loads absent so callers can retry.
            # Listen only from here on, so restoring the saved fields does not write them back.
            self._listeners[scope] = on_change(lambda: export_state(created), partial(self._stage, scope))
            self._models[scope] = created
            return created

    def get_existing(self, scope: ScopeT) -> SharedT | None:
        """Return the model already loaded for scope, or None. Reads nothing."""
        return self._models.get(scope)

    def active(self) -> Mapping[ScopeT, SharedT]:
        """Snapshot the loaded models. Inspection only, so it stays synchronous."""
        return MappingProxyType(dict(self._models))

    async def delete(self, scope: ScopeT) -> SharedT | None:
        """Retire a cached handle and detach its persistence listener, preserving stored data.

        Wait for queued writes to be attempted before returning. A later load restores the
        stored value into a new handle. The retired handle remains reactive, but its future
        commits cannot overwrite the new handle's slot. Return None if the scope was not loaded.
        """
        async with self._load_lock:
            retired = self._models.get(scope)
            if retired is None:
                return None
            # Finish the old generation's writes before making its scope available
            # for hydration. Cancellation leaves the existing handle attached.
            await self.flush()
            self._detach(scope)
            del self._models[scope]
            return retired

    async def clear(self) -> None:
        """Retire every cached handle as delete does, preserving the stored values."""
        async with self._load_lock:
            await self.flush()
            for scope in tuple(self._models):
                self._detach(scope)
            self._models.clear()

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

    def _detach(self, scope: ScopeT) -> None:
        listener = self._listeners.pop(scope, None)
        if listener is not None:
            listener.close()

    def _stage(self, scope: ScopeT, values: Mapping[str, object]) -> None:
        if self._closing:
            # Queuing here would drop the write silently: no worker is left to drain it.
            # A namespace outliving its pool is the host's mistake, so it is reported like
            # any other persistence failure rather than raised into the action.
            self._report(RuntimeError(f"persisted pool is closed; dropped a write for scope {scope!r}"))
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
