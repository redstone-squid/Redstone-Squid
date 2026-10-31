"""One-shot reactive operations observed by synchronous component renders."""

import asyncio
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Any, Protocol, overload

from squid_reactive.completion import Completion
from squid_reactive.resources import AsyncBinding, PendingPolicy, _observe


class OperationOwner(Protocol):
    """The behaviour a bound operation needs from its declaring component."""

    __dict__: dict[str, Any]

    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Pending[ProgressT]:
    """An operation that has not reached a terminal outcome."""

    progress: ProgressT


@dataclass(frozen=True, slots=True)
class Succeeded[ValueT]:
    """An operation that returned a value."""

    value: ValueT


@dataclass(frozen=True, slots=True)
class Failed[ProgressT]:
    """An operation that raised an ordinary exception."""

    error: Exception
    progress: ProgressT


@dataclass(frozen=True, slots=True)
class Cancelled[ProgressT]:
    """An operation whose owning task was cancelled."""

    progress: ProgressT


type OperationStatus[ValueT, ProgressT] = (
    Pending[ProgressT] | Succeeded[ValueT] | Failed[ProgressT] | Cancelled[ProgressT]
)


class Progress[ProgressT]:
    """The explicit capability through which an operation reports reactive progress."""

    def __init__(self, operation: Operation[Any, ProgressT]) -> None:
        self._operation = operation

    def set(self, value: ProgressT) -> None:
        """Replace the operation's current progress and request a render."""
        self._operation._set_progress(value)


class Operation[ValueT, ProgressT](AsyncBinding):
    """One component-bound effect with a synchronous, terminal status."""

    pending_policy = PendingPolicy.EXPLICIT
    reconcile_while_pending = True
    settle_without_delivery = True

    def __init__(
        self,
        owner: OperationOwner,
        loader: Callable[[Progress[ProgressT]], Awaitable[ValueT]],
        *,
        name: str,
        initial: ProgressT,
    ) -> None:
        self._owner = owner
        self._loader = loader
        self._label = name
        self._status: OperationStatus[ValueT, ProgressT] = Pending(initial)
        self._completion: Completion[OperationStatus[ValueT, ProgressT]] = Completion()
        self._started = False

    @property
    def status(self) -> OperationStatus[ValueT, ProgressT]:
        """Return the current progress or terminal outcome."""
        return self._status

    @property
    def pending(self) -> bool:
        """Whether this operation still requests its one settlement attempt."""
        return isinstance(self._status, Pending)

    def _set_progress(self, value: ProgressT) -> None:
        match self._status:
            case Pending():
                self._status = Pending(value)
                self._owner.invalidate()
            case _:
                message = f"operation {self._label!r} has already settled"
                raise RuntimeError(message)

    def __await__(self) -> Generator[Any, None, ValueT]:
        """Run or join the operation under the caller's task."""
        return self._awaited().__await__()

    async def _awaited(self) -> ValueT:
        await self._load()
        match self._status:
            case Succeeded(value):
                return value
            case Failed(error):
                raise error
            case Cancelled():
                raise asyncio.CancelledError
            case Pending():
                message = f"operation {self._label!r} did not settle"
                raise RuntimeError(message)

    async def _load(self) -> OperationStatus[ValueT, ProgressT]:
        """Run once or join the caller currently running this operation."""
        if self._started:
            if not self._completion.done:
                await self._completion.wait()
            return self._status

        self._started = True
        progress = Progress(self)
        try:
            value = await self._loader(progress)
        except asyncio.CancelledError:
            current = self._status
            assert isinstance(current, Pending)
            self._status = Cancelled(current.progress)
            self._owner.invalidate()
            self._completion.cancel()
            raise
        except Exception as error:
            current = self._status
            assert isinstance(current, Pending)
            self._status = Failed(error, current.progress)
            self._owner.invalidate()
            self._completion.resolve(self._status)
        else:
            self._status = Succeeded(value)
            self._owner.invalidate()
            self._completion.resolve(self._status)
        return self._status


class _OperationDescriptor[OwnerT: OperationOwner, ValueT, ProgressT]:
    """Bind one operation per component instance."""

    def __init__(
        self,
        loader: Callable[[OwnerT, Progress[ProgressT]], Awaitable[ValueT]],
        *,
        initial: ProgressT,
    ) -> None:
        self.loader = loader
        self.initial = initial
        self.public_name = loader.__name__
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__operation_{name}"

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _OperationDescriptor[OwnerT, ValueT, ProgressT]: ...

    @overload
    def __get__(self, instance: OwnerT, owner: type | None = None) -> Operation[ValueT, ProgressT]: ...

    def __get__(
        self, instance: OwnerT | None, owner: type | None = None
    ) -> _OperationDescriptor[OwnerT, ValueT, ProgressT] | Operation[ValueT, ProgressT]:
        if instance is None:
            return self
        bound = instance.__dict__.get(self._name)
        if bound is None:
            bound = Operation(
                instance,
                lambda progress: self.loader(instance, progress),
                name=f"{type(instance).__name__}.{self.public_name}",
                initial=self.initial,
            )
            instance.__dict__[self._name] = bound
        _observe(bound)
        return bound


def operation[OwnerT: OperationOwner, ValueT, ProgressT](
    *, initial: ProgressT
) -> Callable[
    [Callable[[OwnerT, Progress[ProgressT]], Awaitable[ValueT]]],
    _OperationDescriptor[OwnerT, ValueT, ProgressT],
]:
    """Declare a one-shot effect whose progress and outcome are rendered synchronously."""

    def decorate(
        loader: Callable[[OwnerT, Progress[ProgressT]], Awaitable[ValueT]],
    ) -> _OperationDescriptor[OwnerT, ValueT, ProgressT]:
        return _OperationDescriptor(loader, initial=initial)

    return decorate


__all__ = [
    "Cancelled",
    "Failed",
    "Operation",
    "OperationStatus",
    "Pending",
    "Progress",
    "Succeeded",
    "operation",
]
