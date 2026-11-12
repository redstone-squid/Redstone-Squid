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
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.contracts import DocumentContent
from squid_ui_discord.request import Request


class _PendingCommand(Component[ComponentsV2Target, Request]):
    """Show a command result, retaining the pending card when the handler returns None."""

    execution: OperationExecution[DocumentContent | None, None]

    def __init__(
        self,
        work: Callable[[], Awaitable[DocumentContent | None]],
        *,
        initial: DocumentContent,
        render_error: Callable[[Exception], DocumentContent] | None,
    ) -> None:
        self._work = work
        self._initial = initial
        self._render_error = render_error
        self.execution = self._run.start()

    @override
    def render(self) -> DocumentContent:
        """Render the pending or terminal result."""
        match self.execution.status:
            case Pending():
                return self._initial
            case Succeeded(value=value):
                return self._initial if value is None else value
            case Failed(error=error):
                if self._render_error is None:
                    return self._initial
                return self._render_error(error)
            case Cancelled():
                return self._initial

    @operation(initial=None)
    async def _run(self, _progress: ProgressReporter[None]) -> DocumentContent | None:
        """Run the callback once the pending card has been delivered."""
        return await self._work()
