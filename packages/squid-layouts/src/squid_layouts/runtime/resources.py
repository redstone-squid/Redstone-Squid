"""Reactive async values observed by synchronous component renders."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, overload


class ResourceOwner(Protocol):
    """The component behavior a bound resource needs."""

    __dict__: dict[str, Any]

    def invalidate(self) -> None: ...


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


class ResourceDelivery(StrEnum):
    """When a frontend may deliver a render that observes a pending resource."""

    VISIBLE = "visible"
    ATOMIC = "atomic"


class ResourceNotReadyError(LookupError):
    """A resource value was read while its state was not ready."""


_CURRENT_RESOURCES: ContextVar[list[Resource[Any]] | None] = ContextVar(
    "squid_layouts_observed_resources", default=None
)


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
    ) -> None:
        self._owner = owner
        self._loader = loader
        self._name = name
        self.delivery = delivery
        self._state: ResourceState[ValueT] = Pending()
        self._loading: tuple[int, asyncio.Event] | None = None
        self._request_token = 0

    @property
    def state(self) -> ResourceState[ValueT]:
        """Return the current synchronous state."""
        return self._state

    @property
    def value(self) -> ValueT:
        """Return the ready value or fail instead of smuggling pending into its type."""
        if isinstance(self.state, Ready):
            return self.state.value
        message = f"resource {self._name!r} is {type(self.state).__name__.lower()}, not ready"
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
        self._state = Pending(_previous(self.state))
        if notify:
            self._owner.invalidate()

    def replace(self, value: ValueT) -> None:
        """Install an authoritative value and supersede every in-flight request."""
        self._request_token += 1
        self._state = Ready(value)
        self._owner.invalidate()

    async def reload(self) -> ResourceState[ValueT]:
        """Request and settle a fresh value under the caller's task."""
        self._invalidate(notify=True)
        return await self._settle()

    async def _settle(self) -> ResourceState[ValueT]:
        """Settle the current pending generation, sharing an identical in-flight load."""
        if not isinstance(self.state, Pending):
            return self.state
        token = self._request_token
        if self._loading is not None and self._loading[0] == token:
            await self._loading[1].wait()
            return self.state

        settled = asyncio.Event()
        self._loading = (token, settled)
        try:
            try:
                value = await self._loader()
            except Exception as error:
                if token == self._request_token:
                    self._state = Failed(error, _previous(self.state))
                    self._owner.invalidate()
            else:
                if token == self._request_token:
                    self._state = Ready(value)
                    self._owner.invalidate()
        finally:
            if self._loading is not None and self._loading[0] == token:
                self._loading = None
            settled.set()
        return self.state


class _ResourceDescriptor[OwnerT: ResourceOwner, ValueT]:
    """Bind one loader and its dependency declaration per component instance."""

    def __init__(
        self,
        loader: Callable[[OwnerT], Awaitable[ValueT]],
        *,
        depends: tuple[object, ...],
        delivery: ResourceDelivery,
    ) -> None:
        self.loader = loader
        self.depends = depends
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
            bound = Resource(
                instance,
                lambda: self.loader(instance),
                name=f"{type(instance).__name__}.{self.public_name}",
                delivery=self.delivery,
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound

    def invalidate_for(self, instance: ResourceOwner) -> None:
        """Invalidate an already-materialized binding after a dependency commit."""
        bound = instance.__dict__.get(self._name)
        if isinstance(bound, Resource):
            bound._invalidate(notify=False)


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    loader: Callable[[OwnerT], Awaitable[ValueT]],
    /,
    *,
    depends: tuple[object, ...] = (),
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> _ResourceDescriptor[OwnerT, ValueT]: ...


@overload
def resource[OwnerT: ResourceOwner, ValueT](
    *,
    depends: tuple[object, ...] = (),
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> Callable[[Callable[[OwnerT], Awaitable[ValueT]]], _ResourceDescriptor[OwnerT, ValueT]]: ...


def resource(
    loader: Callable[[ResourceOwner], Awaitable[Any]] | None = None,
    /,
    *,
    depends: tuple[object, ...] = (),
    delivery: ResourceDelivery = ResourceDelivery.VISIBLE,
) -> Any:
    """Declare a lazy async value whose current state is available during synchronous render."""

    def decorate(
        function: Callable[[ResourceOwner], Awaitable[Any]],
    ) -> _ResourceDescriptor[ResourceOwner, Any]:
        return _ResourceDescriptor(function, depends=depends, delivery=delivery)

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
