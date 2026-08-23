"""Reactive async values observed by synchronous component renders."""

import asyncio
from collections.abc import Awaitable, Callable, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, overload

from squid_reactive.core import (
    _CONSUMER,
    ReactiveCycleError,
    _bump_epoch,
    action_participant,
    cycle_path,
    declared_cells,
    join_action,
    settling,
)


class ResourceOwner(Protocol):
    """The behaviour a bound resource needs from whatever declared it."""

    __dict__: dict[str, Any]

    def invalidate(self) -> None: ...


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


type ResourceState[ValueT] = Pending[ValueT] | Ready[ValueT] | Failed[ValueT]
type AtomicResourceState[ValueT] = Ready[ValueT] | Failed[ValueT]


class ResourceDelivery(StrEnum):
    """When a frontend may deliver a render that observes a pending resource."""

    VISIBLE = "visible"
    ATOMIC = "atomic"


class ResourceNotReadyError(LookupError):
    """A resource value was read while its state was not ready."""


class _AtomicResourcePending(ResourceNotReadyError):
    """Abort a discovery render until an atomic resource has settled."""

    def __init__(self, resource: Resource[Any]) -> None:
        self.resource = resource
        super().__init__(f"atomic resource {resource._label!r} is pending")


_CURRENT_RESOURCES: ContextVar[list[Resource[Any]] | None] = ContextVar(
    "squid_layouts_observed_resources", default=None
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

    __slots__ = ("_baseline", "_resource", "value")

    def __init__(self, resource: Resource[Any]) -> None:
        self._baseline: dict[Any, int] | None = None
        self._resource = resource
        self.value: Any = _MISSING

    def prepare(self) -> None:
        """Settle every source while the action can still roll back."""
        if not isinstance(self.value, _Missing):
            self._baseline = {source: source.settle() for source in self._resource.sources}

    def apply(self) -> None:
        if not isinstance(self.value, _Missing):
            assert self._baseline is not None
            self._resource._replace_now(self.value, baseline=self._baseline)

    def abort(self) -> None:
        self._baseline = None
        self.value = _MISSING

    def finalize(self) -> None:
        """Installing already invalidated the owner, which is the only watcher there is."""


def _previous[ValueT](state: ResourceState[ValueT]) -> Ready[ValueT] | None:
    if isinstance(state, Ready):
        return state
    return state.previous


class Resource[ValueT]:
    """One component-bound async value with synchronous observable state."""

    def __init__(
        self,
        owner: ResourceOwner,
        loader: Callable[[], Awaitable[ValueT]],
        *,
        name: str,
        delivery: ResourceDelivery,
        address: Any = None,
        publish: Callable[[Any], None] | None = None,
    ) -> None:
        self._owner = owner
        self._loader = loader
        self._label = name
        self.delivery = delivery
        self.address = address
        """Where this resource's changes are published, or `None` for a component's own.

        Mirrors `_Cell.address`: present exactly when something other than the owner might
        be looking, which for a resource means it was declared on an `sl.Shared` namespace.
        """
        self._publish = publish
        self._state: ResourceState[ValueT] = Pending()
        self._loading: tuple[int, asyncio.Event] | None = None
        self._request_token = 0
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
    def state(self) -> ResourceState[ValueT]:
        """Return the current synchronous state, re-pending it if what it read has moved."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            # This action already declared the value authoritative, so nothing it reads next
            # should re-pend it -- and `_recheck` would empty the sources `apply` re-baselines.
            return Ready(staged)
        self._recheck()
        self.track()
        return self._state

    def track(self) -> None:
        """Record a read of this resource with whatever is consuming reads.

        The same call `_Cell.read` makes, and for the same reason: a value derived from this
        one has to know when this one moves. It is what lets one resource derive from
        another, and a computed derive from a resource.
        """
        consumer = _CONSUMER.get()
        if consumer is not None and consumer is not self:
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
        short-circuits on the epoch, so without it a `sl.computed` would never re-derive.
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
        self._rechecking = True
        try:
            moved = self.sources and any(source.settle() != seen for source, seen in self.sources.items())
        finally:
            self._rechecking = False
        if moved:
            self.sources = {}
            self._invalidate(notify=False)

    @property
    def value(self) -> ValueT:
        """Return the ready value or fail instead of smuggling pending into its type."""
        state = self.state
        if isinstance(state, Ready):
            return state.value
        # Reading the value of a resource whose loader you are inside is a cycle, not bad
        # luck: it cannot become ready while it is waiting on you. Say that, rather than
        # reporting a pending resource and leaving the ring to be worked out.
        path = cycle_path(self)
        if path is not None:
            raise ReactiveCycleError(path)
        message = f"resource {self._label!r} is {type(state).__name__.lower()}, not ready"
        raise ResourceNotReadyError(message)

    @property
    def pending(self) -> bool:
        """Whether this resource currently requests settlement."""
        return isinstance(self.state, Pending)

    def invalidate(self) -> None:
        """Request a fresh value while retaining the last successful one."""
        self._invalidate(notify=True)

    def _invalidate(self, *, notify: bool) -> None:
        self._request_token += 1
        self._state = Pending(_previous(self._state))
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
        self._request_token += 1
        self._state = Ready(value)
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
        if isinstance(self._state, Pending):
            await self._load()
        # Tracked after settling, never before: recording the version this resource held
        # while still pending would leave the caller stale against the value it just waited
        # for, and re-pend it the moment anyone looked.
        self.track()
        state = self._state
        if isinstance(state, Failed):
            raise state.error
        if isinstance(state, Ready):
            return state.value
        message = f"resource {self._label!r} did not settle"
        raise ResourceNotReadyError(message)

    async def reload(self) -> ResourceState[ValueT]:
        """Request and settle a fresh value under the caller's task."""
        self._invalidate(notify=True)
        return await self._load()

    async def _load(self) -> ResourceState[ValueT]:
        """Settle the current pending generation, sharing an identical in-flight load.

        Reads `_state` rather than `state` throughout, here and below. The public read
        tracks, and tracking from inside the machinery would register whoever is loading
        against the version this resource holds *before* it settles -- leaving them stale
        against the value they are about to receive. Only `_awaited` tracks, and only once
        the value is in hand.
        """
        self._recheck()
        if not isinstance(self._state, Pending):
            return self._state
        # Before the shared wait below, which for a cycle would be this load waiting on
        # itself -- a hang with nothing to report rather than an error naming the ring.
        with settling(self):
            return await self._loaded()

    async def _loaded(self) -> ResourceState[ValueT]:
        token = self._request_token
        if self._loading is not None and self._loading[0] == token:
            await self._loading[1].wait()
            return self._state

        settled = asyncio.Event()
        self._loading = (token, settled)
        self.sources = {}
        consumer = _CONSUMER.set(self)
        try:
            try:
                value = await self._loader()
            except Exception as error:
                if token == self._request_token:
                    self._state = Failed(error, _previous(self._state))
                    self._landed()
                    self._notify()
            else:
                if token == self._request_token:
                    self._state = Ready(value)
                    self._landed()
                    self._notify()
        finally:
            _CONSUMER.reset(consumer)
            if self._loading is not None and self._loading[0] == token:
                self._loading = None
            settled.set()
        return self._state


class AtomicResource[ValueT](Resource[ValueT]):
    """A resource whose render-visible state is always settled.

    The mount catches a pending read during discovery, settles the resource, and retries the
    render. Direct reads before a mount has settled the resource raise ``ResourceNotReadyError``.
    """

    @property
    def state(self) -> AtomicResourceState[ValueT]:
        state = super().state
        if isinstance(state, Pending):
            if state.previous is not None:
                return state.previous
            raise _AtomicResourcePending(self)
        return state

    @property
    def pending(self) -> bool:
        """Whether this resource still needs settlement without exposing pending state."""
        staged = self._staged()
        if not isinstance(staged, _Missing):
            return False
        self._recheck()
        return isinstance(self._state, Pending)

    async def reload(self) -> AtomicResourceState[ValueT]:
        self._invalidate(notify=True)
        state = await self._load()
        if isinstance(state, Pending):
            message = f"atomic resource {self._label!r} did not settle"
            raise ResourceNotReadyError(message)
        return state


class _ResourceDescriptor[OwnerT: ResourceOwner, ValueT]:
    """Bind one loader per component instance."""

    def __init__(
        self,
        loader: Callable[[OwnerT], Awaitable[ValueT]],
        *,
        delivery: ResourceDelivery,
    ) -> None:
        self.loader = loader
        self.delivery = delivery
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
                delivery=self.delivery,
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
                delivery=self.delivery,
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
    delivery: Literal[ResourceDelivery.ATOMIC],
) -> _AtomicResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> _ResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    *,
    delivery: Literal[ResourceDelivery.ATOMIC],
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _AtomicResourceDescriptor[OwnerT, ValueT]]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    *,
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _ResourceDescriptor[OwnerT, ValueT]]: ...


def resource(
    loader: Callable[[ResourceOwner], Awaitable[Any]] | None = None,
    /,
    *,
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> Any:
    """Declare a lazy async value whose current state is available during synchronous render.

    The loader's reads are tracked, so the state it consults is its dependency set and a
    write to any of it re-pends the resource at the next read.
    """

    def decorate(
        function: Callable[[ResourceOwner], Awaitable[Any]],
    ) -> _ResourceDescriptor[ResourceOwner, Any] | _AtomicResourceDescriptor[ResourceOwner, Any]:
        descriptor = _AtomicResourceDescriptor if delivery is ResourceDelivery.ATOMIC else _ResourceDescriptor
        return descriptor(function, delivery=delivery)

    return decorate if loader is None else decorate(loader)


def _observe(resource: Resource[Any]) -> None:
    observed = _CURRENT_RESOURCES.get()
    if observed is not None:
        observed.append(resource)


@contextmanager
def observe_resources() -> Iterator[list[Resource[Any]]]:
    """Collect resources accessed by one expanded component render."""
    observed: list[Resource[Any]] = []
    token = _CURRENT_RESOURCES.set(observed)
    try:
        yield observed
    finally:
        _CURRENT_RESOURCES.reset(token)


def unique_resources(resources: list[Resource[Any]]) -> tuple[Resource[Any], ...]:
    """Preserve render order while removing repeat accesses to the same binding."""
    seen: set[int] = set()
    unique: list[Resource[Any]] = []
    for resource in resources:
        identity = id(resource)
        if identity not in seen:
            seen.add(identity)
            unique.append(resource)
    return tuple(unique)
