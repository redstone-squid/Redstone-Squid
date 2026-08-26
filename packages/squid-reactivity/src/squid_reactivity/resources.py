"""Reactive async values observed by synchronous component renders."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal, Protocol, overload

from squid_reactivity.actions import (
    DEFAULT_REDACTION,
    ActionId,
    CausalRef,
    ExceptionReport,
    ResourceEventSnapshot,
    current_causality,
    emit_causal_event,
)
from squid_reactivity.completion import Completion
from squid_reactivity.core import (
    _CONSUMER,
    ReactiveCycleError,
    ReactiveOwner,
    TransactionView,
    _bump_epoch,
    action_participant,
    cycle_path,
    declared_cells,
    join_action,
    settling,
)


class ResourceOwner(ReactiveOwner, Protocol):
    """The behaviour a bound resource needs from whatever declared it."""

    __dict__: dict[str, Any]

    def invalidate(self) -> None: ...


class AsyncBinding(Protocol):
    """A caller-owned asynchronous value discovered during a synchronous render."""

    pending_mode: PendingMode
    reconcile_while_pending: bool
    settle_without_delivery: bool

    @property
    def pending(self) -> bool: ...

    async def _load(self) -> object: ...


class AddressedOwner(Protocol):
    """An owner whose resources are named, so their changes can be published.

    A component's resource is private to one instance and needs no address: the mount
    re-renders it and nobody else is looking. A namespace's resource is shared by every
    mount holding the namespace, so a reload has to reach the others the way a shared cell
    write does -- by publishing an address they follow.
    """

    def _resource_binding(self, name: str) -> tuple[Any, Callable[[Any], None]]:
        """The address this resource publishes under, and what to publish it with."""
        ...


class LoadScope(Protocol):
    """A cancellable region around one generation's loader, entered in the loading task.

    Ends when the loader returns or raises, or when `cancel` is called because the generation
    was superseded; exiting swallows only the cancellation this scope itself delivered.
    `anyio.CancelScope` satisfies this as it stands, which is what `sd` installs --
    this package has no dependencies, so it supplies the seam rather than the cancellation.
    """

    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...

    def cancel(self) -> None: ...


class _NoAbandonment:
    """The region a load gets when nothing is installed: it runs to completion regardless.

    Stateless and shared, because it ends nothing and holds nothing to end.
    """

    __slots__ = ()

    def __enter__(self) -> _NoAbandonment:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return False

    def cancel(self) -> None:
        """Nothing to stop: the loader keeps its own completion and only its result is dropped."""


_NO_ABANDONMENT = _NoAbandonment()

_ABANDONMENT: ContextVar[Callable[[], LoadScope] | None] = ContextVar("squid_reactivity_load_abandonment", default=None)


@contextmanager
def abandon_superseded_loads(scope: Callable[[], LoadScope]) -> Iterator[None]:
    """Cancel a resource load whose generation is superseded, for loads started in this context.

    Read when a load starts rather than when a resource is constructed: a resource is built
    lazily on first access, during a render that may be nowhere near the task that later loads
    it. Context variables copy into child tasks, so one installation covers a whole settle group.

    Without it a superseded loader runs to completion and only its result is discarded, which is
    correct -- the contract makes a loader safe to run zero, one or many times -- just wasteful.
    """
    token = _ABANDONMENT.set(scope)
    try:
        yield
    finally:
        _ABANDONMENT.reset(token)


def _load_scope() -> LoadScope:
    factory = _ABANDONMENT.get()
    return _NO_ABANDONMENT if factory is None else factory()


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
    """Whether pending is explicit in the render contract or settled atomically."""

    EXPLICIT = "explicit"
    ATOMIC = "atomic"


class ResourceNotReadyError(LookupError):
    """A resource value was read while its state was not ready."""


class _AtomicResourcePending(ResourceNotReadyError):
    """Abort a discovery render until an atomic resource has settled."""

    def __init__(self, resource: Resource[Any]) -> None:
        self.resource = resource
        super().__init__(f"atomic resource {resource._label!r} is pending")


_CURRENT_BINDINGS: ContextVar[list[AsyncBinding] | None] = ContextVar(
    "squid_reactivity_observed_async_bindings", default=None
)


class _Missing:
    """A staged replacement of `None` is a value; the absence of one is not."""

    __slots__ = ()


_MISSING = _Missing()


class _Replacement:
    """One resource's replaced value, held until the action that made it commits.

    Keyed by the resource itself, so repeated `replace` calls in one action collapse into
    the last one, exactly as repeated writes to a state cell do.
    """

    __slots__ = ("_resource", "value")

    def __init__(self, resource: Resource[Any]) -> None:
        self._resource = resource
        self.value: Any = _MISSING

    def prepare(self, view: TransactionView) -> dict[Any, int] | None:
        """Settle every source while the action can still roll back.

        `None` means this participant staged no replacement, which is why `apply` can be
        total: there is no state it has to check before trusting what it was handed.
        """
        if isinstance(self.value, _Missing):
            return None
        return {source: source.settle() for source in self._resource.sources}

    def apply(self, prepared: dict[Any, int] | None) -> None:
        if prepared is not None:
            self._resource._replace_now(self.value, baseline=prepared)

    def describe_change(self, prepared: dict[Any, int] | None) -> None:
        return None

    def abort(self, prepared: dict[Any, int] | None, cause: BaseException) -> None:
        self.value = _MISSING

    def finalize(self, prepared: dict[Any, int] | None) -> None:
        """Installing already invalidated the owner, which is the only watcher there is."""


class _Load:
    """One generation's private notebook: what it read, how to stop it, and how to wake joiners.

    The reads are held here rather than on the resource because a superseded loader does not
    stop at the bump. It resumes inside its own task with its own `_CONSUMER` still pointing
    here -- until its next checkpoint where `abandon_superseded_loads` is installed, and to
    completion where it is not -- and goes on recording, so a single dict on the resource would
    collect the reads of every generation that ever started, and the live value would end up
    subscribed to state it never looked at. Only the generation that still holds the token
    publishes what it read.
    """

    __slots__ = ("completion", "owner", "scope", "sources", "token")

    def __init__(self, owner: Resource[Any], token: int, completion: Completion[Any], scope: LoadScope) -> None:
        # `owner` is what keeps `Resource.track` able to recognise a self-read. While the load
        # runs it, not the resource, is the `_CONSUMER`, so an identity check against the
        # consumer alone would let a loader that reads its own resource subscribe the resource
        # to itself -- which re-pends it forever rather than settling.
        self.owner = owner
        self.token = token
        self.completion = completion
        self.scope = scope
        self.sources: dict[Any, int] = {}


def _previous[ValueT](status: ResourceStatus[ValueT]) -> Ready[ValueT] | None:
    if isinstance(status, Ready):
        return status
    return status.previous


class Resource[ValueT](AsyncBinding):
    """One component-bound async value with synchronous observable state."""

    reconcile_while_pending = False
    settle_without_delivery = False

    def __init__(
        self,
        owner: ResourceOwner,
        loader: Callable[[], Awaitable[ValueT]],
        *,
        name: str,
        pending_mode: PendingMode,
        address: Any = None,
        publish: Callable[[Any], None] | None = None,
    ) -> None:
        self._owner = owner
        self._loader = loader
        self._label = name
        self.pending_mode = pending_mode
        self.address = address
        """Where this resource's changes are published, or `None` for a component's own.

        Mirrors `_Cell.address`: present exactly when something other than the owner might
        be looking, which for a resource means it was declared on a `Shared` namespace.
        """
        self._publish = publish
        self._status: ResourceStatus[ValueT] = Pending()
        self._loading: _Load | None = None
        self._request_token = 0
        self._generation_id = uuid.uuid7()
        causality = current_causality()
        self._generation_cause = None if causality is None else causality[0]
        self._generation_root = None if causality is None else causality[1]
        self._rechecking = False
        self.version = 0
        """Dates this resource's state, so a reader can tell whether it has moved.

        Every transition moves it: a load reaching `Ready` or `Failed`, a `replace`, and a
        re-pend. Including the re-pend is what lets an invalidation reach a dependent
        immediately rather than one load later, and it is safe because a dependent awaits its
        input rather than racing it -- see `__await__`.
        """
        self.sources: dict[Any, int] = declared_cells(owner)
        """State the last load read, and the version each held. Filled by tracking, not declared.

        Seeded with everything the component declares, because a resource whose loader has not
        run yet cannot say what it reads and may still be handed a value by `replace`.
        """

    @property
    def status(self) -> ResourceStatus[ValueT]:
        """Return the current synchronous state, re-pending it if what it read has moved."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            # This action already declared the value authoritative, so nothing it reads next
            # should re-pend it -- and `_recheck` would empty the sources `apply` re-baselines.
            return Ready(staged)
        self._recheck()
        self.track()
        return self._status

    def track(self) -> None:
        """Record a read of this resource with whatever is consuming reads.

        The same call `_Cell.read` makes, and for the same reason: a value derived from this
        one has to know when this one moves. It is what lets one resource derive from
        another, and a computed derive from a resource.
        """
        consumer = _CONSUMER.get()
        if consumer is None or consumer is self:
            return
        if isinstance(consumer, _Load) and consumer.owner is self:
            # This resource's own load is reading it. A dependency on itself would make every
            # install invalidate the value it just installed.
            return
        consumer.sources[self] = self.version

    def settle(self) -> int:
        """The version a reader should compare against, as `_Cell.settle` does for a cell.

        Re-checks first, so asking whether this resource moved also propagates a move from
        whatever *it* reads. That is what carries an invalidation down a chain: the topic
        moves, this resource re-pends, and its dependent sees a new version to compare
        against.
        """
        self._recheck()
        return self.version

    def _landed(self) -> None:
        """A new value is installed: date it, and tell anyone following this address.

        Only where a value lands -- `Ready`, `Failed`, `replace` -- and never on a re-pend.
        Publishing a re-pend would wake every follower to look at a value that is still on
        its way, and the reload that follows publishes anyway.
        """
        self._moved()
        if self.address is not None and self._publish is not None:
            self._publish(self.address)

    def _notify(self) -> None:
        """Tell whoever is watching that this resource moved.

        A component's resource invalidates its component, the only thing looking at it. A
        namespace's resource has already published its address, and the namespace renders
        nothing itself, so the publish *is* the notification and there is nobody else to tell.
        """
        if self.address is None:
            self._owner.invalidate()

    def _moved(self) -> None:
        """Date this resource's state anew, and tell settled readers to look again.

        The epoch half matters as much as the version: a computed that reads this resource
        short-circuits on the epoch, so without it a computed value would never re-derive.
        """
        self.version += 1
        _bump_epoch()

    def _recheck(self) -> None:
        """Give up a value whose inputs moved since the load that produced it.

        Pulled here rather than pushed at commit: the write that moved the input already
        invalidated the owner, so the only thing left is for the next reader to notice.

        Re-entrant through a chain -- asking a source whether it moved re-checks it too -- so
        a cycle is broken by reporting the version in hand rather than recursing forever. The
        cycle itself is caught in `_load`, where it can be named.
        """
        if self._rechecking:
            return
        # While a load is in flight, its own notebook is the live read set: `self.sources`
        # describes the settled value's inputs and is emptied for the duration, so consulting
        # it here would make a move in something the loader has *already read* invisible. The
        # generation would then run to completion and install a value built from inputs that
        # have since changed, and nothing would supersede it.
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
        """Return the ready value or fail instead of smuggling pending into its type."""
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
        """Install an authoritative value and supersede every in-flight request.

        Inside an action this stages like any other write: the action reads back what it
        replaced, nobody else sees it until the commit lands, and a rollback drops it. A
        resource is the application's value, so it may not be the one thing that survives a
        handler that failed.
        """
        staged = join_action(self, lambda: _Replacement(self))
        if staged is None:
            self._replace_now(value)
            return
        staged.value = value

    def _replace_now(self, value: ValueT, *, baseline: dict[Any, int] | None = None) -> None:
        if baseline is None:
            baseline = {source: source.settle() for source in self.sources}
        self._new_generation()
        self._status = Ready(value)
        self._landed()
        # Re-baselined rather than dropped: an authoritative value is current for the inputs
        # as they stand now, and a later change to one of them should still reload.
        self.sources = baseline
        self._notify()

    def _staged(self) -> ValueT | _Missing:
        """This action's replacement for this resource, if it made one."""
        staged = action_participant(self)
        return staged.value if isinstance(staged, _Replacement) else _MISSING

    def __await__(self) -> Generator[Any, None, ValueT]:
        """`value = await self.other` inside a loader: settle that resource, then use it.

        A resource derived from another resource has to wait for it, and `value` cannot --
        it is synchronous, and raises for a pending resource because a render has nowhere to
        wait. Awaiting is the loader's version: it settles the dependency if it is pending,
        registers the read so a later change re-pends this resource too, and raises whatever
        the dependency raised.

        Only meaningful inside another resource's loader, or anywhere else already async.
        A render cannot await, which is the point: a render reads what has settled.
        """
        return self._awaited().__await__()

    async def _awaited(self) -> ValueT:
        self._recheck()
        if isinstance(self._status, Pending):
            await self._load()
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
        return await self._load()

    async def _load(self) -> ResourceStatus[ValueT]:
        """Settle the current pending generation, sharing an identical in-flight load.

        Reads `_status` rather than `status` throughout, here and below. The public read
        tracks, and tracking from inside the machinery would register whoever is loading
        against the version this resource holds *before* it settles -- leaving them stale
        against the value they are about to receive. Only `_awaited` tracks, and only once
        the value is in hand.
        """
        self._recheck()
        if not isinstance(self._status, Pending):
            return self._status
        # Before the shared wait below, which for a cycle would be this load waiting on
        # itself -- a hang with nothing to report rather than an error naming the ring.
        with settling(self):
            return await self._loaded()

    async def _loaded(self) -> ResourceStatus[ValueT]:
        token = self._request_token
        if self._loading is not None and self._loading.token == token:
            await self._loading.completion.wait()
            if isinstance(self._status, Pending):
                return await self._loaded()
            return self._status

        settled: Completion[ResourceStatus[ValueT]] = Completion()
        load = _Load(self, token, settled, _load_scope())
        self._loading = load
        generation = (self._generation_id, self._generation_cause, self._generation_root)
        self._emit_generation("started", generation)
        # Emptied here as well as tracked into `load`, because `_recheck` reads this one and
        # must stay inert while a load is in flight: re-pending mid-load would supersede the
        # very generation that is about to answer.
        self.sources = {}
        consumer = _CONSUMER.set(load)
        try:
            try:
                # A sentinel rather than a flag, so the abandoned branch below is the one place
                # `value` is known not to exist: `load.scope` swallows the cancellation it
                # delivered, and the loader never got to return anything.
                value: ValueT | _Missing = _MISSING
                with load.scope:
                    value = await self._loader()
            except asyncio.CancelledError as error:
                # A cancellation from outside this generation's scope, which is the caller's to
                # answer for. Install nothing and let it through.
                self._emit_generation("cancelled", generation, error)
                raise
            except Exception as error:
                if token == self._request_token:
                    self.sources = load.sources
                    self._status = Failed(error, _previous(self._status))
                    self._landed()
                    self._notify()
                    self._emit_generation("failed", generation, error)
                else:
                    self._emit_generation("superseded", generation)
            else:
                if isinstance(value, _Missing):
                    # Abandoned rather than superseded: `_new_generation` stopped this loader
                    # instead of letting it finish. The generation that replaced it is already
                    # `Pending`, so there is nothing to install and nothing to say about state.
                    self._emit_generation("abandoned", generation)
                elif token == self._request_token:
                    self.sources = load.sources
                    self._status = Ready(value)
                    self._landed()
                    self._notify()
                    self._emit_generation("ready", generation)
                else:
                    self._emit_generation("superseded", generation)
        finally:
            _CONSUMER.reset(consumer)
            if self._loading is load:
                self._loading = None
            if not settled.done:
                settled.resolve(self._status)
        return self._status

    def _new_generation(self) -> None:
        self._request_token += 1
        if self._loading is not None:
            # The bump just superseded whatever is in flight, so ask it to stop. The single
            # place that needs to: `_invalidate` and `_replace_now` are the only two callers,
            # which is both ways a generation can be superseded. Without an installed scope
            # this is inert and the loader runs to completion as before.
            self._loading.scope.cancel()
        self._generation_id = uuid.uuid7()
        causality = current_causality()
        self._generation_cause = None if causality is None else causality[0]
        self._generation_root = None if causality is None else causality[1]

    def _emit_generation(
        self,
        status: str,
        generation: tuple[uuid.UUID, CausalRef | None, ActionId | None],
        error: BaseException | None = None,
    ) -> None:
        generation_id, cause, root_action_id = generation
        exception = None if error is None else DEFAULT_REDACTION.redact_exception(ExceptionReport.capture(error))
        emit_causal_event(
            ResourceEventSnapshot(
                str(generation_id),
                None if root_action_id is None else str(root_action_id),
                cause,
                self._label,
                status,
                datetime.now(UTC),
                exception,
            )
        )


class AtomicResource[ValueT](Resource[ValueT]):
    """A resource whose render-visible state is always settled.

    The mount catches a pending read during discovery, settles the resource, and retries the
    render. Direct reads before a mount has settled the resource raise ``ResourceNotReadyError``.
    """

    @property
    def status(self) -> AtomicResourceStatus[ValueT]:
        status = super().status
        if isinstance(status, Pending):
            if status.previous is not None:
                return status.previous
            raise _AtomicResourcePending(self)
        return status

    @property
    def pending(self) -> bool:
        """Whether this resource still needs settlement without exposing pending state."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            return False
        self._recheck()
        return isinstance(self._status, Pending)

    async def reload(self) -> AtomicResourceStatus[ValueT]:
        self._invalidate(notify=True)
        status = await self._load()
        if isinstance(status, Pending):
            message = f"atomic resource {self._label!r} did not settle"
            raise ResourceNotReadyError(message)
        return status


class _ResourceDescriptor[OwnerT: ResourceOwner, ValueT]:
    _reactive_resource_descriptor = True

    """Bind one loader per component instance."""

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
            # Asked once per instance, on the binding that caches: an owner that addresses
            # its resources says so, and a component simply does not have the hook.
            binding = getattr(instance, "_resource_binding", None)
            address, publish = binding(self.public_name) if binding is not None else (None, None)
            bound = Resource(
                instance,
                lambda: self.loader(instance),
                name=f"{type(instance).__name__}.{self.public_name}",
                pending_mode=self.pending_mode,
                address=address,
                publish=publish,
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound


class _AtomicResourceDescriptor[OwnerT: ResourceOwner, ValueT](_ResourceDescriptor[OwnerT, ValueT]):
    """Bind an atomic resource while preserving its narrowed descriptor type."""

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _AtomicResourceDescriptor[OwnerT, ValueT]: ...

    @overload
    def __get__(self, instance: OwnerT, owner: type | None = None) -> AtomicResource[ValueT]: ...

    def __get__(
        self, instance: OwnerT | None, owner: type | None = None
    ) -> _AtomicResourceDescriptor[OwnerT, ValueT] | AtomicResource[ValueT]:
        if instance is None:
            return self
        bound = instance.__dict__.get(self._name)
        if bound is None:
            binding = getattr(instance, "_resource_binding", None)
            address, publish = binding(self.public_name) if binding is not None else (None, None)
            bound = AtomicResource(
                instance,
                lambda: self.loader(instance),
                name=f"{type(instance).__name__}.{self.public_name}",
                pending_mode=self.pending_mode,
                address=address,
                publish=publish,
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    pending: Literal[PendingMode.ATOMIC],
) -> _AtomicResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> _ResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    *,
    pending: Literal[PendingMode.ATOMIC],
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _AtomicResourceDescriptor[OwnerT, ValueT]]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _ResourceDescriptor[OwnerT, ValueT]]: ...


def resource(
    loader: Callable[[ResourceOwner], Awaitable[Any]] | None = None,
    /,
    *,
    pending: PendingMode = PendingMode.EXPLICIT,
) -> Any:
    """Declare a lazy async value whose current state is available during synchronous render.

    The loader's reads are tracked, so the state it consults is its dependency set and a
    write to any of it re-pends the resource at the next read.
    """

    def decorate(
        function: Callable[[ResourceOwner], Awaitable[Any]],
    ) -> _ResourceDescriptor[ResourceOwner, Any] | _AtomicResourceDescriptor[ResourceOwner, Any]:
        descriptor = _AtomicResourceDescriptor if pending is PendingMode.ATOMIC else _ResourceDescriptor
        return descriptor(function, pending_mode=pending)

    return decorate if loader is None else decorate(loader)


def _observe(binding: AsyncBinding) -> None:
    observed = _CURRENT_BINDINGS.get()
    if observed is not None:
        observed.append(binding)


@contextmanager
def observe_async_bindings() -> Iterator[list[AsyncBinding]]:
    """Collect asynchronous bindings accessed by one expanded component render."""
    observed: list[AsyncBinding] = []
    token = _CURRENT_BINDINGS.set(observed)
    try:
        yield observed
    finally:
        _CURRENT_BINDINGS.reset(token)


def unique_async_bindings(bindings: list[AsyncBinding]) -> tuple[AsyncBinding, ...]:
    """Preserve render order while removing repeat accesses to the same binding."""
    seen: set[int] = set()
    unique: list[AsyncBinding] = []
    for binding in bindings:
        identity = id(binding)
        if identity not in seen:
            seen.add(identity)
            unique.append(binding)
    return tuple(unique)


# Compatibility for integrations still naming the resource-only implementation detail.
observe_resources = observe_async_bindings
unique_resources = unique_async_bindings
