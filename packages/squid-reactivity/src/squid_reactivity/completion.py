"""Caller-owned asynchronous completion shared by resources and operations."""

import asyncio


class Completion[ValueT]:
    """A one-shot future its owner settles: `resolve()` publishes the value, `cancel()` wakes waiters with an error.

    `cancel()` raises `asyncio.CancelledError` in every waiter. Binds to the running loop on first use and never
    owns the work that settles it. Both verbs are no-ops once settled.
    """

    def __init__(self) -> None:
        self._future: asyncio.Future[ValueT] | None = None

    def _bound(self) -> asyncio.Future[ValueT]:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
        return self._future

    @property
    def done(self) -> bool:
        """True once resolved or cancelled."""
        return self._future is not None and self._future.done()

    @property
    def cancelled(self) -> bool:
        return self._future is not None and self._future.cancelled()

    def resolve(self, value: ValueT) -> None:
        future = self._bound()
        if not future.done():
            future.set_result(value)

    def cancel(self) -> None:
        future = self._bound()
        if not future.done():
            future.cancel()

    async def wait(self) -> ValueT:
        """Shielded, so one cancelled waiter does not cancel the completion for the others."""
        return await asyncio.shield(self._bound())


__all__ = ["Completion"]
