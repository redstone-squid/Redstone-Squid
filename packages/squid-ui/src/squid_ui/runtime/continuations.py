"""Run requested work only after the action that requested it commits.

event.on_success creates a PostCommitOperation handle during an action handler.
The execution host starts the callback after a successful commit; rollback
discards it. The handle lets other code observe completion without owning the
callback's lifetime.
"""

import logging
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from squid_reactivity.actions import ActionCommit, ActionRollback
from squid_reactivity.completion import Completion
from squid_reactivity.internals import CURRENT_TRANSACTION, Signal
from squid_reactivity.operations import (
    Failed,
    OperationContext,
    OperationDefinition,
    OperationExecution,
    OperationStatus,
    Pending,
    ProgressReporter,
)
from squid_ui.errors import SquidUiError
from squid_ui.runtime.tasks import anyio, task_is_within

if TYPE_CHECKING:
    from squid_ui.runtime.services import UIExecutionHost

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Discarded:
    """An operation whose action rolled back before the operation could start."""


class ActionDiscardedError(SquidUiError, RuntimeError):
    """The action rolled back, so its requested operation never ran."""


class PostCommitOperation[ValueT]:
    """Observe scheduled work until its host completes it or its action rolls back.

    Obtain this handle from ``event.on_success()``. ``status`` is initially Pending;
    after commit, ``context`` identifies the operation and the action that caused it.
    A rollback makes the status Discarded and ``wait()`` raises ActionDiscardedError.

    Waiting observes the host's execution; it never starts the callback. Cancelling a
    waiter leaves the operation running. Wait outside the action handler, since the
    handler must return before the transaction can commit.
    """

    def __init__(self, callback: Callable[[], Awaitable[ValueT]], *, name: str) -> None:
        self._callback: Callable[[], Awaitable[ValueT]] | None = callback
        self._name = name
        self._signal = Signal()
        self._execution: OperationExecution[ValueT, None] | None = None
        self._discarded = False
        self._start_error: Exception | None = None
        self._done: Completion[None] = Completion()
        self._runner: int | None = None

    @property
    def context(self) -> OperationContext | None:
        """The committed operation's identity, or None before admission."""
        self._signal.track()
        return None if self._execution is None else self._execution.context

    @property
    def status(self) -> OperationStatus[ValueT, None] | Discarded:
        self._signal.track()
        if self._discarded:
            return Discarded()
        if self._start_error is not None:
            return Failed(self._start_error, None)
        # Observing the underlying AsyncBinding would let a renderer start its loader.
        # Track status separately because this execution belongs exclusively to the host.
        if self._execution is None:
            return Pending(None)
        self._execution._signal.track()
        return self._execution._status

    def __await__(self) -> Generator[Any, None, ValueT]:
        return self.wait().__await__()

    async def wait(self) -> ValueT:
        """Observe the result, raising the callback's failure or ActionDiscardedError."""
        if not self._done.done:
            current = CURRENT_TRANSACTION.get()
            if current is not None and not current.closed:
                message = "wait for after-commit work outside the action transaction"
                raise RuntimeError(message)
            if self._runner is not None and task_is_within({self._runner}):
                message = "an after-commit operation cannot wait for its own completion"
                raise RuntimeError(message)
        await self._done.wait()
        if self._discarded:
            message = "the action rolled back; its after-commit operation was discarded"
            raise ActionDiscardedError(message)
        if self._start_error is not None:
            raise self._start_error
        assert self._execution is not None
        return await self._execution

    def _commit(self, commit: ActionCommit, host: UIExecutionHost) -> None:
        definition = OperationDefinition(self._invoke, name=self._name, initial=None)
        self._execution = definition.start(
            cause=commit.context.causal_ref(), root_action_id=commit.context.root_action_id
        )
        self._signal.notify()
        try:
            host.start_operation(self._run)
        except Exception as error:
            self._start_error = error
            self._callback = None
            self._execution._emit("failed", error)
            self._done.resolve(None)
            self._signal.notify()
            _logger.exception("could not start after-commit operation %s", self._name)

    def _discard(self, rollback: ActionRollback) -> None:
        self._discarded = True
        self._signal.notify()
        self._callback = None
        self._done.resolve(None)

    async def _invoke(self, progress: ProgressReporter[None]) -> ValueT:
        callback, self._callback = self._callback, None
        assert callback is not None
        return await callback()

    async def _run(self) -> None:
        self._runner = anyio.get_current_task().id
        try:
            assert self._execution is not None
            await self._execution
        except anyio.get_cancelled_exc_class():
            # The execution records cancellation; it must not cancel the host's task group.
            pass
        except Exception:
            _logger.exception("after-commit operation %s failed", self._name)
        finally:
            self._runner = None
            self._done.resolve(None)
