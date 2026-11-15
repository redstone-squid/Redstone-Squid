"""Cache asynchronous values and invalidate them when their reactive inputs change.

A resource exposes Pending, Ready, or Failed status synchronously. Reading status
never starts I/O; await the resource or call resolve() to load it. Reads performed
by the loader become dependencies, so changed inputs make a later read request
a new value.

Each invalidation or replacement starts a generation. Only a load for the current
generation can install its result, preventing old requests from overwriting new data.
UI integrations can discover resources during rendering and resolve them afterward.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Generator, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal, Protocol, overload, override

from squid_reactivity._runtime import _bump_epoch
from squid_reactivity.addresses import CellAddress, SubscriberRegistry, SubscriptionSource, publish
from squid_reactivity.async_values import (
    AsyncBinding as AsyncBinding,
)
from squid_reactivity.async_values import (
    _Execution,
    _observe,
)
from squid_reactivity.async_values import (
    observe_async_bindings as observe_async_bindings,
)
from squid_reactivity.async_values import (
    unique_async_bindings as unique_async_bindings,
)
from squid_reactivity.core import (
    declared_cells,
)
from squid_reactivity.diagnostics import (
    ResourceEventSnapshot,
    emit_causal_event,
    has_event_observers,
)
from squid_reactivity.errors import (
    ReactiveCycleError,
    ReactivityError,
)
from squid_reactivity.evaluation import _SettlingNode, cycle_path, settling
from squid_reactivity.tracking import _DEPENDENCY_COLLECTOR, _DependencyCollector, _ReactiveSource
from squid_reactivity.transactions import TransactionParticipant, TransactionView, enlist, transaction_participant
from squid_reactivity.transactions.context import (
    CausalRef,
    ExceptionReport,
    TransactionId,
    current_causality,
)
from squid_reactivity.transactions.state import ReactiveStateOwner


class LoadScope(Protocol):
    """A cancellation scope entered in the task running one load.

    Cancel stops a superseded loader. Exiting the scope must suppress only
    cancellation raised by that scope. Applications can supply anyio.CancelScope
    without making this package depend on anyio.
    """

    __slots__ = ()

    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...

    def cancel(self) -> None: ...


class _NoAbandonment(LoadScope):
    """Let superseded loaders finish when no cancellation scope is installed."""

    __slots__ = ()

    @override
    def __enter__(self) -> _NoAbandonment:
        return self

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return False

    @override
    def cancel(self) -> None:
        """Leave the loader running; its obsolete result will be discarded."""


_NO_LOAD_SCOPE_FACTORY = _NoAbandonment()

_LOAD_SCOPE_FACTORY: ContextVar[Callable[[], LoadScope] | None] = ContextVar(
    "squid_reactivity_load_abandonment", default=None
)
"""Cancellation-scope factory inherited by nested resource awaits and child tasks.

The first caller that starts a load supplies the factory; the load retains the resulting
scope until completion. Joiners do not replace it. Keep the factory scoped because a loader
can await resources unknown to its caller; passing policy only to the outer resolve would
leave those nested loads uncancellable. None preserves the dependency-free default.
"""


@contextmanager
def abandon_superseded_loads(scope: Callable[[], LoadScope]) -> Iterator[None]:
    """Install cancellation scopes for loads started within this context.

    The factory is consulted when loading starts, not when the resource is
    created. Child tasks inherit it through the context. Each superseded load
    is cancelled through its own scope.

    Without a factory, obsolete loaders run to completion and their results are
    discarded. Loaders must be safe to repeat or abandon in either mode.
    """
    token = _LOAD_SCOPE_FACTORY.set(scope)
    try:
        yield
    finally:
        _LOAD_SCOPE_FACTORY.reset(token)


def _load_scope() -> LoadScope:
    factory = _LOAD_SCOPE_FACTORY.get()
    return _NO_LOAD_SCOPE_FACTORY if factory is None else factory()


@dataclass(frozen=True, slots=True)
class Ready[ValueT]:
    """A resource value that completed for its current dependencies."""

    value: ValueT


@dataclass(frozen=True, slots=True)
class Pending[ValueT]:
    """A resource waiting to load, optionally retaining its last ready value."""

    previous: Ready[ValueT] | None = None


@dataclass(frozen=True, slots=True)
class Failed[ValueT]:
    """A resource load that failed, optionally retaining its last ready value."""

    error: Exception
    previous: Ready[ValueT] | None = None


type ResourceStatus[ValueT] = Pending[ValueT] | Ready[ValueT] | Failed[ValueT]
type AtomicResourceStatus[ValueT] = Ready[ValueT] | Failed[ValueT]


class PendingMode(StrEnum):
    """Choose whether synchronous status exposes Pending or requires a ready value."""

    EXPLICIT = "explicit"
    ATOMIC = "atomic"


class ResourceNotReadyError(ReactivityError, LookupError):
    """A resource value was read while its state was not ready."""


class _AtomicResourcePending(ResourceNotReadyError):
    """Request loading before a synchronous computation can continue.

    UI integrations catch this internal ResourceNotReadyError subtype, resolve the
    resource, and retry the computation.
    """

    def __init__(self, resource: Resource[Any]) -> None:
        self.resource = resource
        super().__init__(f"atomic resource {resource._label!r} is pending")


class _Missing:
    """Distinguish an absent replacement from a replacement whose value is None."""

    __slots__ = ()


_MISSING = _Missing()


class _Replacement(TransactionParticipant[dict[_ReactiveSource, int] | None]):
    """Stage one resource replacement as a transaction participant.

    Repeated replace() calls in one transaction keep only the final value. Preparation
    captures dependency versions; publication installs the replacement.
    """

    __slots__ = ("_resource", "value")

    def __init__(self, resource: Resource[Any]) -> None:
        self._resource = resource
        self.value: Any = _MISSING

    @override
    def prepare(self, view: TransactionView) -> dict[_ReactiveSource, int] | None:
        """Capture dependency versions before publishing a replacement.

        Return None when no replacement was staged; otherwise apply() uses these versions
        as the replacement's dependency baseline.
        """
        if isinstance(self.value, _Missing):
            return None
        return {source: source.settle() for source in self._resource.sources}

    @override
    def apply(self, prepared: dict[_ReactiveSource, int] | None) -> None:
        if prepared is not None:
            self._resource._replace_now(self.value, baseline=prepared)

    @override
    def describe_change(self, prepared: dict[_ReactiveSource, int] | None) -> None:
        return None

    @override
    def abort(self, prepared: dict[_ReactiveSource, int] | None, cause: BaseException) -> None:
        self.value = _MISSING

    @override
    def finalize(self, prepared: dict[_ReactiveSource, int] | None) -> None:
        """Do nothing: applying the replacement already sent its notifications."""


class _Load[ValueT](_DependencyCollector):
    """Keep one load's dependencies, cancellation scope, and completion signal.

    A superseded loader may continue running until cancellation reaches it, or
    until completion when no cancellation scope exists. Its tracked reads must
    remain separate from the next load's reads. Only the load whose token is
    still current installs its dependencies on the resource.
    """

    __slots__ = ("execution", "owner", "scope", "sources", "token")

    def __init__(
        self,
        owner: Resource[ValueT],
        token: int,
        execution: _Execution[ResourceStatus[ValueT]],
        scope: LoadScope,
    ) -> None:
        # `owner` is what keeps `Resource.track` able to recognise a self-read. While the load
        # runs it, not the resource, is the `_DEPENDENCY_COLLECTOR`, so an identity check against the
        # consumer alone would let a loader that reads its own resource subscribe the resource
        # to itself -- which re-pends it forever rather than settling.
        self.owner = owner
        self.token = token
        self.execution = execution
        self.scope = scope
        self.sources: dict[_ReactiveSource, int] = {}


def _previous[ValueT](status: ResourceStatus[ValueT]) -> Ready[ValueT] | None:
    if isinstance(status, Ready):
        return status
    return status.previous


class Resource[ValueT](AsyncBinding[ResourceStatus[ValueT]], _ReactiveSource, _SettlingNode, SubscriptionSource):
    """A cached asynchronous value with tracked inputs and synchronous status access.

    Reading status never starts a load. Await the resource to obtain its value, or call
    resolve() to obtain its status. Concurrent callers share the current load. Changes
    to inputs read by the loader make a later dependency check mark the resource pending.

    Loaders may run again or have their results discarded. Use operations for explicitly
    started effects that should not repeat merely because a dependency changed.
    """

    def __init__(
        self,
        owner: ReactiveStateOwner,
        loader: Callable[[], Awaitable[ValueT]],
        *,
        name: str,
        pending_mode: PendingMode,
        address: CellAddress | None = None,
    ) -> None:
        self._loader = loader
        self._label = name
        self.pending_mode = pending_mode
        self._reactive_subscribers: SubscriberRegistry = {}
        self._address = address if address is not None else CellAddress(self, "status")
        self._status: ResourceStatus[ValueT] = Pending()
        self._loading: _Load[ValueT] | None = None
        self._request_token = 0
        self._generation_id = uuid.uuid7()
        causality = current_causality()
        self._generation_cause = None if causality is None else causality.cause
        self._generation_root = None if causality is None else causality.root_transaction_id
        self._rechecking = False
        self.version = 0
        """Version checked by dependent resources and computed values.

        Loading a value, recording a failure, replacing a value, and marking it pending
        all advance the version. Dependents can therefore notice invalidation before
        the next load finishes.
        """
        self.sources: dict[_ReactiveSource, int] = {
            source: version for source, version in declared_cells(owner).items()
        }
        """State the last load read, and the version each held. Filled by tracking, not declared.

        Seeded with everything the model declares, because a resource whose loader has not
        run yet cannot say what it reads and may still be handed a value by `replace`.
        """

    @property
    @override
    def address(self) -> CellAddress:
        return self._address

    @property
    def status(self) -> ResourceStatus[ValueT]:
        """Return the status, marking it pending if a dependency changed."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            # This action already declared the value authoritative, so nothing it reads next
            # should re-pend it -- and `_recheck` would empty the sources `apply` re-baselines.
            return Ready(staged)
        self._recheck()
        self.track()
        return self._status

    def track(self) -> None:
        """Record this resource and its version as a dependency of the current reader."""
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is None or consumer is self:
            return
        if isinstance(consumer, _Load) and consumer.owner is self:
            # This resource's own load is reading it. A dependency on itself would make every
            # install invalidate the value it just installed.
            return
        consumer.sources[self] = self.version

    @override
    def dependencies(self) -> Iterable[_ReactiveSource]:
        return self.sources

    @override
    def settle(self) -> int:
        """Recheck dependencies and return the resulting version without starting a load."""
        self._recheck()
        return self.version

    def _notify(self) -> None:
        """Notify readers of a status change, including an explicit reload becoming pending."""
        publish(self.address)

    def _moved(self) -> None:
        """Advance the resource version and the epoch used by computed caches."""
        self.version += 1
        _bump_epoch()

    def _recheck(self) -> None:
        """Mark the resource pending when any dependency version changes.

        Dependencies are checked on read rather than at commit. Recursive checks
        stop at this resource while it is already checking; resolve detects and
        reports dependency cycles when a loader runs.
        """
        if self._rechecking:
            return
        # During loading, check the dependencies already read by that load. self.sources
        # belongs to the previous value and is empty while loading, so checking it would
        # miss changes to inputs the active loader has used.
        watched = self._loading.sources if self._loading is not None else self.sources
        self._rechecking = True
        try:
            # Snapshotted: `settle()` re-checks a source, which can reach back into this
            # resource, and a load still recording would otherwise resize the dict under us.
            moved = watched and any(source.settle() != seen for source, seen in tuple(watched.items()))
        finally:
            self._rechecking = False
        if moved:
            self.sources = {}
            self._invalidate(notify=False)

    @property
    def value(self) -> ValueT:
        """Return a ready value or raise ResourceNotReadyError.

        A read that would depend on the currently running loader raises
        ReactiveCycleError instead. This property does not wait for a load.
        """
        status = self.status
        if isinstance(status, Ready):
            return status.value
        # Reading the value of a resource whose loader you are inside is a cycle, not bad
        # luck: it cannot become ready while it is waiting on you. Say that, rather than
        # reporting a pending resource and leaving the ring to be worked out.
        path = cycle_path(self)
        if path is not None:
            raise ReactiveCycleError(path)
        message = f"resource {self._label!r} is {type(status).__name__.lower()}, not ready"
        raise ResourceNotReadyError(message)

    @property
    @override
    def pending(self) -> bool:
        """Whether this resource currently requests settlement."""
        return isinstance(self.status, Pending)

    def invalidate(self) -> None:
        """Request a fresh value while retaining the last successful one."""
        self._invalidate(notify=True)

    def _invalidate(self, *, notify: bool) -> None:
        self._new_generation()
        self._status = Pending(_previous(self._status))
        self._moved()
        if notify:
            self._notify()

    def replace(self, value: ValueT) -> None:
        """Supply a value and supersede any load in progress.

        Inside a transaction, its reads see the staged replacement while other readers
        keep seeing the committed value. Rollback discards the replacement. Outside a
        transaction, the value is installed immediately.
        """
        staged = enlist(self, lambda: _Replacement(self))
        if staged is None:
            self._replace_now(value)
            return
        staged.value = value

    def _replace_now(self, value: ValueT, *, baseline: dict[_ReactiveSource, int] | None = None) -> None:
        if baseline is None:
            baseline = {source: source.settle() for source in self.sources}
        self._new_generation()
        self._status = Ready(value)
        self._moved()
        # Re-baselined rather than dropped: an authoritative value is current for the inputs
        # as they stand now, and a later change to one of them should still reload.
        self.sources = baseline
        self._notify()

    def _staged(self) -> ValueT | _Missing:
        """Return the active transaction's replacement for this resource, if present."""
        staged = transaction_participant(self)
        return staged.value if isinstance(staged, _Replacement) else _MISSING

    def __await__(self) -> Generator[Any, None, ValueT]:
        """Load the resource if needed, then return its value or raise its error.

        A resource loader may await another resource and track it as a dependency. Tracking
        happens after loading so the caller records the version it actually receives.
        Synchronous consumers can inspect status or value instead.
        """
        return self._awaited().__await__()

    async def _awaited(self) -> ValueT:
        self._recheck()
        if isinstance(self._status, Pending):
            await self.resolve()
        # Tracked after settling, never before: recording the version this resource held
        # while still pending would leave the caller stale against the value it just waited
        # for, and re-pend it the moment anyone looked.
        self.track()
        status = self._status
        if isinstance(status, Failed):
            raise status.error
        if isinstance(status, Ready):
            return status.value
        message = f"resource {self._label!r} did not settle"
        raise ResourceNotReadyError(message)

    async def reload(self) -> ResourceStatus[ValueT]:
        """Request and settle a fresh value under the caller's task."""
        self._invalidate(notify=True)
        return await self.resolve()

    @override
    async def resolve(self) -> ResourceStatus[ValueT]:
        """Load or join the current pending generation and return its status.

        Use _status internally to avoid tracking the resource's pending version as
        a dependency of its caller. _awaited records the read after loading finishes.
        """
        self._recheck()
        if not isinstance(self._status, Pending):
            return self._status
        # Check for a cycle before joining a load, or the loader could wait on itself.
        with settling(self):
            return await self._loaded()

    async def _loaded(self) -> ResourceStatus[ValueT]:
        token = self._request_token
        if self._loading is not None and self._loading.token == token:
            await self._loading.execution.completion.wait()
            if isinstance(self._status, Pending):
                return await self._loaded()
            return self._status

        execution: _Execution[ResourceStatus[ValueT]] = _Execution()
        load = _Load(self, token, execution, _load_scope())
        self._loading = load
        return await execution.run(lambda: self._execute_load(load), lambda: self._status)

    async def _execute_load(self, load: _Load[ValueT]) -> ResourceStatus[ValueT]:
        token = load.token
        generation = (self._generation_id, self._generation_cause, self._generation_root)
        self._emit_generation("started", generation)
        # The active load tracks its own dependencies. Retire the old value's dependencies
        # so they do not remain subscribed after this generation installs its result.
        self.sources = {}
        consumer = _DEPENDENCY_COLLECTOR.set(load)
        try:
            try:
                # A sentinel rather than a flag, so the abandoned branch below is the one place
                # `value` is known not to exist: `load.scope` swallows the cancellation it
                # delivered, and the loader never got to return anything.
                value: ValueT | _Missing = _MISSING
                with load.scope:
                    value = await self._loader()
            except asyncio.CancelledError as error:
                # Preserve external cancellation so the caller's task or deadline can handle it.
                self._emit_generation("cancelled", generation, error)
                raise
            except Exception as error:
                if token == self._request_token:
                    self.sources = load.sources
                    self._status = Failed(error, _previous(self._status))
                    self._moved()
                    self._notify()
                    self._emit_generation("failed", generation, error)
                else:
                    self._emit_generation("superseded", generation)
            else:
                if isinstance(value, _Missing):
                    # The scope cancelled this loader before it returned. Keep the newer
                    # generation's state, which may already have finished loading.
                    self._emit_generation("abandoned", generation)
                elif token == self._request_token:
                    self.sources = load.sources
                    self._status = Ready(value)
                    self._moved()
                    self._notify()
                    self._emit_generation("ready", generation)
                else:
                    self._emit_generation("superseded", generation)
        finally:
            _DEPENDENCY_COLLECTOR.reset(consumer)
            if self._loading is load:
                self._loading = None
        return self._status

    def _new_generation(self) -> None:
        self._request_token += 1
        if self._loading is not None:
            # Invalidation and replacement both come through here. Stop the obsolete load
            # if a cancellation scope is installed; otherwise its result is discarded later.
            self._loading.scope.cancel()
        self._generation_id = uuid.uuid7()
        causality = current_causality()
        self._generation_cause = None if causality is None else causality.cause
        self._generation_root = None if causality is None else causality.root_transaction_id

    def _emit_generation(
        self,
        status: str,
        generation: tuple[uuid.UUID, CausalRef | None, TransactionId | None],
        error: BaseException | None = None,
    ) -> None:
        if not has_event_observers():
            return
        generation_id, cause, root_transaction_id = generation
        exception = None if error is None else ExceptionReport.capture(error)
        emit_causal_event(
            ResourceEventSnapshot(
                str(generation_id),
                None if root_transaction_id is None else str(root_transaction_id),
                cause,
                self._label,
                status,
                datetime.now(UTC),
                exception,
            )
        )


class AtomicResource[ValueT](Resource[ValueT]):
    """Expose Ready or Failed status, using the last ready value while loading.

    When pending without a previous value, reading status raises ResourceNotReadyError.
    The pending property still reports whether a load is needed, and resolve() can load it.

    UI integrations catch the internal pending exception, resolve the resource, and retry
    rendering. Other callers can await it directly.
    """

    @property
    @override
    def status(self) -> AtomicResourceStatus[ValueT]:
        status = super().status
        if isinstance(status, Pending):
            if status.previous is not None:
                return status.previous
            raise _AtomicResourcePending(self)
        return status

    @property
    @override
    def pending(self) -> bool:
        """Whether this resource still needs settlement without exposing pending state."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            return False
        self._recheck()
        return isinstance(self._status, Pending)

    @override
    async def reload(self) -> AtomicResourceStatus[ValueT]:
        self._invalidate(notify=True)
        status = await self.resolve()
        if isinstance(status, Pending):
            message = f"atomic resource {self._label!r} did not settle"
            raise ResourceNotReadyError(message)
        return status


class _ResourceDescriptor[OwnerT: ReactiveStateOwner, ValueT]:
    """Bind a loader to a separate resource on each model instance."""

    _reactive_resource_descriptor = True

    def __init__(
        self,
        loader: Callable[[OwnerT], Awaitable[ValueT]],
        *,
        pending_mode: PendingMode,
    ) -> None:
        self.loader = loader
        self.pending_mode = pending_mode
        self.public_name = loader.__name__
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__resource_{name}"

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _ResourceDescriptor[OwnerT, ValueT]: ...

    @overload
    def __get__(self, instance: OwnerT, owner: type | None = None) -> Resource[ValueT]: ...

    def __get__(
        self, instance: OwnerT | None, owner: type | None = None
    ) -> _ResourceDescriptor[OwnerT, ValueT] | Resource[ValueT]:
        if instance is None:
            return self
        bound = instance.__dict__.get(self._name)
        if bound is None:
            bound = Resource(
                instance,
                lambda: self.loader(instance),
                name=f"{type(instance).__name__}.{self.public_name}",
                pending_mode=self.pending_mode,
                address=CellAddress(instance, self.public_name),
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound


class _AtomicResourceDescriptor[OwnerT: ReactiveStateOwner, ValueT](_ResourceDescriptor[OwnerT, ValueT]):
    """Bind an atomic resource while preserving its narrowed descriptor type."""

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _AtomicResourceDescriptor[OwnerT, ValueT]: ...

    @overload
    def __get__(self, instance: OwnerT, owner: type | None = None) -> AtomicResource[ValueT]: ...

    @override
    def __get__(
        self, instance: OwnerT | None, owner: type | None = None
    ) -> _AtomicResourceDescriptor[OwnerT, ValueT] | AtomicResource[ValueT]:
        if instance is None:
            return self
        bound = instance.__dict__.get(self._name)
        if bound is None:
            bound = AtomicResource(
                instance,
                lambda: self.loader(instance),
                name=f"{type(instance).__name__}.{self.public_name}",
                pending_mode=self.pending_mode,
                address=CellAddress(instance, self.public_name),
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound


@overload
def resource[OwnerT: ReactiveStateOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    pending: Literal[PendingMode.ATOMIC],
) -> _AtomicResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ReactiveStateOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> _ResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ReactiveStateOwner, ValueT](
    *,
    pending: Literal[PendingMode.ATOMIC],
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _AtomicResourceDescriptor[OwnerT, ValueT]]: ...


@overload
def resource[OwnerT: ReactiveStateOwner, ValueT](
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _ResourceDescriptor[OwnerT, ValueT]]: ...


def resource(
    loader: Callable[[Any], Awaitable[Any]] | None = None,
    /,
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> Any:
    """Declare a cached asynchronous value whose inputs are tracked automatically.

    The loader's reactive reads become dependencies. When their versions change, a later
    read marks the resource pending. Await the bound resource to load its value, or call
    resolve() to load and inspect its status. Loaders may repeat and obsolete results
    may be discarded; use operations for explicitly started effects.

    EXPLICIT mode exposes Pending, Ready, or Failed status. ATOMIC mode uses the last ready
    value while reloading and raises ResourceNotReadyError when no value is available.
    UI integrations can use that exception to load required inputs before rendering.
    """

    def decorate(
        function: Callable[[Any], Awaitable[Any]],
    ) -> _ResourceDescriptor[ReactiveStateOwner, Any] | _AtomicResourceDescriptor[ReactiveStateOwner, Any]:
        descriptor = _AtomicResourceDescriptor if pending is PendingMode.ATOMIC else _ResourceDescriptor
        return descriptor(function, pending_mode=pending)

    return decorate if loader is None else decorate(loader)
