"""The component behind a command's pending card."""

from collections.abc import Awaitable, Callable
from typing import override

from squid_reactivity.operations import (
    Cancelled,
    Failed,
    OperationExecution,
    Pending,
    ProgressReporter,
    Succeeded,
    operation,
)
from squid_ui.document import DocumentLike
from squid_ui.runtime.component import Component


class _ManagedResult[ValueT](Component):
    """Expose a callback's pending and terminal states as a component."""

    execution: OperationExecution[ValueT, None]

    def __init__(
        self,
        work: Callable[[], Awaitable[ValueT]],
        *,
        initial: DocumentLike,
        render_success: Callable[[ValueT], DocumentLike],
        render_error: Callable[[Exception], DocumentLike] | None,
    ) -> None:
        self._work = work
        self._initial = initial
        self._render_success = render_success
        self._render_error = render_error
        self.execution = self._run.start()

    @property
    def initial(self) -> DocumentLike:
        """Return the scene shown while the callback is running."""
        return self._initial

    @property
    def render_error(self) -> Callable[[Exception], DocumentLike] | None:
        """Return the optional failure renderer."""
        return self._render_error

    @override
    def render(self) -> DocumentLike:
        """Render the pending or terminal result."""
        match self.execution.status:
            case Pending():
                return self._initial
            case Succeeded(value=value):
                return self._render_success(value)
            case Failed(error=error):
                if self._render_error is None:
                    return self._initial
                return self._render_error(error)
            case Cancelled():
                return self._initial

    @operation(initial=None)
    async def _run(self, _progress: ProgressReporter[None]) -> ValueT:
        """Run the callback once the initial scene has been delivered."""
        return await self._work()
