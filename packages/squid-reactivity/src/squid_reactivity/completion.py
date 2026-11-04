"""Share an asynchronous result without letting one waiter cancel other waiters."""

import asyncio


class Completion[ValueT]:
    """A result that its owner can resolve or cancel once.

    The first resolve, cancel, or wait call binds it to the running event loop.
    Cancelling a waiter leaves the shared result available to other waiters.
    """

    def __init__(self) -> None:
        self._future: asyncio.Future[ValueT] | None = None

    def _bound(self) -> asyncio.Future[ValueT]:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
        return self._future

    @property
    def done(self) -> bool:
        """Whether the result has been resolved or cancelled."""
        return self._future is not None and self._future.done()

    @property
    def cancelled(self) -> bool:
        """Whether the owner cancelled the result before resolving it."""
        return self._future is not None and self._future.cancelled()

    def resolve(self, value: ValueT) -> None:
        """Set the result, or do nothing if it has already resolved or been cancelled."""
        future = self._bound()
        if not future.done():
            future.set_result(value)

    def cancel(self) -> None:
        """Cancel the shared result and wake waiters with CancelledError."""
        future = self._bound()
        if not future.done():
            future.cancel()

    async def wait(self) -> ValueT:
        """Wait without letting a cancelled joiner cancel every other observer."""
        return await asyncio.shield(self._bound())


__all__ = ["Completion"]
