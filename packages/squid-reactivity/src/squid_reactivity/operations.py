"""Repeatable operation definitions and one-shot causally identified executions."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, overload

from squid_reactivity.actions import (
    DEFAULT_REDACTION,
    ActionContext,
    ActionId,
    ActionKind,
    CausalRef,
    ExceptionReport,
    OperationEventSnapshot,
    causal_scope,
    current_action,
    emit_causal_event,
)
from squid_reactivity.completion import Completion
from squid_reactivity.resources import AsyncBinding, PendingMode, _observe


class OperationOwner(Protocol):
    """The behaviour a bound operation definition needs from its declaring owner."""

    __dict__: dict[str, Any]

    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Stable execution identity and its initiating causal relation."""

    execution_id: uuid.UUID
    cause: CausalRef | None
    root_action_id: ActionId | None
    name: str

    def causal_ref(self) -> CausalRef:
        return CausalRef("operation", str(self.execution_id))


@dataclass(frozen=True, slots=True)
class Pending[ProgressT]:
    """An operation execution that has not reached a terminal outcome."""

    progress: ProgressT


@dataclass(frozen=True, slots=True)
class Succeeded[ValueT]:
    """An operation execution that returned a value."""

    value: ValueT


@dataclass(frozen=True, slots=True)
class Failed[ProgressT]:
    """An operation execution that raised an ordinary exception."""

    error: Exception
    progress: ProgressT


@dataclass(frozen=True, slots=True)
class Cancelled[ProgressT]:
    """An operation execution whose owning task was cancelled."""

    progress: ProgressT


type OperationStatus[ValueT, ProgressT] = (
    Pending[ProgressT] | Succeeded[ValueT] | Failed[ProgressT] | Cancelled[ProgressT]
)


class Progress[ProgressT]:
    """The explicit capability through which an execution reports reactive progress."""

    def __init__(self, execution: OperationExecution[Any, ProgressT]) -> None:
        self._execution = execution

    def set(self, value: ProgressT) -> None:
        """Replace current progress and request a render."""
        self._execution._set_progress(value)


class OperationExecution[ValueT, ProgressT](AsyncBinding):
    """One one-shot execution. Awaiting it runs or joins it until terminal status."""

    pending_mode = PendingMode.EXPLICIT
    reconcile_while_pending = True
    settle_without_delivery = True

    def __init__(
        self,
        owner: OperationOwner,
        loader: Callable[[Progress[ProgressT]], Awaitable[ValueT]],
        *,
        context: OperationContext,
        initial: ProgressT,
    ) -> None:
        self._owner = owner
        self._loader = loader
        self.context = context
        self._status: OperationStatus[ValueT, ProgressT] = Pending(initial)
        self._completion: Completion[OperationStatus[ValueT, ProgressT]] = Completion()
        self._started = False

    @property
    def status(self) -> OperationStatus[ValueT, ProgressT]:
        """Return current progress or the terminal outcome and register observation."""
        _observe(self)
        return self._status

    @property
    def pending(self) -> bool:
        """Whether this execution still requests its one settlement attempt."""
        return isinstance(self._status, Pending)

    def _set_progress(self, value: ProgressT) -> None:
        match self._status:
            case Pending():
                self._status = Pending(value)
                self._owner.invalidate()
            case _:
                message = f"operation execution {self.context.execution_id} has already settled"
                raise RuntimeError(message)

    def __await__(self) -> Generator[Any, None, ValueT]:
        """Run or join this execution under the caller's owned task."""
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
                message = f"operation {self.context.name!r} did not settle"
                raise RuntimeError(message)

    async def _load(self) -> OperationStatus[ValueT, ProgressT]:
        if self._started:
            if not self._completion.done:
                await self._completion.wait()
            return self._status
        self._started = True
        progress = Progress(self)
        try:
            with causal_scope(self.context.causal_ref(), self.context.root_action_id):
                value = await self._loader(progress)
        except asyncio.CancelledError:
            current = self._status
            assert isinstance(current, Pending)
            self._status = Cancelled(current.progress)
            self._owner.invalidate()
            self._completion.cancel()
            self._emit("cancelled")
            raise
        except Exception as error:
            current = self._status
            assert isinstance(current, Pending)
            self._status = Failed(error, current.progress)
            self._owner.invalidate()
            self._completion.resolve(self._status)
            self._emit("failed", error)
        else:
            self._status = Succeeded(value)
            self._owner.invalidate()
            self._completion.resolve(self._status)
            self._emit("succeeded")
        return self._status

    @contextmanager
    def start_action(self, name: str, *, kind: ActionKind = ActionKind.SYSTEM):
        """Start a fresh state-publishing action caused by this execution."""
        from squid_reactivity.core import fresh_action_transaction

        context = ActionContext.create(
            name,
            kind=kind,
            cause=self.context.causal_ref(),
            root_action_id=self.context.root_action_id,
        )
        with fresh_action_transaction(action_context=context):
            yield context

    def _emit(self, status: str, error: BaseException | None = None) -> None:
        report = None if error is None else DEFAULT_REDACTION.redact_exception(ExceptionReport.capture(error))
        emit_causal_event(
            OperationEventSnapshot(
                str(self.context.execution_id),
                None if self.context.root_action_id is None else str(self.context.root_action_id),
                self.context.cause,
                self.context.name,
                status,
                datetime.now(UTC),
                report,
            )
        )


class OperationDefinition[ValueT, ProgressT]:
    """A repeatable bound definition that starts a fresh one-shot execution each time."""

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
        self.name = name
        self.initial = initial

    def start(
        self,
        *,
        cause: CausalRef | None = None,
        root_action_id: ActionId | None = None,
    ) -> OperationExecution[ValueT, ProgressT]:
        """Create a fresh execution causally linked to the current action by default."""
        action = current_action()
        if action is not None:
            cause = cause or action.causal_ref()
            root_action_id = root_action_id or action.root_action_id
        context = OperationContext(uuid.uuid7(), cause, root_action_id, self.name)
        execution = OperationExecution(self._owner, self._loader, context=context, initial=self.initial)
        execution._emit("started")
        return execution


class _OperationDescriptor[OwnerT: OperationOwner, ValueT, ProgressT]:
    """Bind one repeatable definition per owner instance."""

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
        self._name = f"__operation_definition_{name}"

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _OperationDescriptor[OwnerT, ValueT, ProgressT]: ...

    @overload
    def __get__(self, instance: OwnerT, owner: type | None = None) -> OperationDefinition[ValueT, ProgressT]: ...

    def __get__(
        self, instance: OwnerT | None, owner: type | None = None
    ) -> _OperationDescriptor[OwnerT, ValueT, ProgressT] | OperationDefinition[ValueT, ProgressT]:
        if instance is None:
            return self
        bound = instance.__dict__.get(self._name)
        if bound is None:
            bound = OperationDefinition(
                instance,
                lambda progress: self.loader(instance, progress),
                name=f"{type(instance).__name__}.{self.public_name}",
                initial=self.initial,
            )
            instance.__dict__[self._name] = bound
        return bound


def operation[OwnerT: OperationOwner, ValueT, ProgressT](
    *, initial: ProgressT
) -> Callable[
    [Callable[[OwnerT, Progress[ProgressT]], Awaitable[ValueT]]],
    _OperationDescriptor[OwnerT, ValueT, ProgressT],
]:
    """Declare a repeatable operation definition."""

    def decorate(
        loader: Callable[[OwnerT, Progress[ProgressT]], Awaitable[ValueT]],
    ) -> _OperationDescriptor[OwnerT, ValueT, ProgressT]:
        return _OperationDescriptor(loader, initial=initial)

    return decorate


__all__ = [
    "Cancelled",
    "Failed",
    "OperationContext",
    "OperationDefinition",
    "OperationExecution",
    "OperationStatus",
    "Pending",
    "Progress",
    "Succeeded",
    "operation",
]
