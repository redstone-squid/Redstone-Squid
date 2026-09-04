"""Caller-owned asynchronous completion shared by resources and operations."""

import asyncio


class Completion[ValueT]:
    """A lazily loop-bound signal that ends when its owner resolves or cancels it."""

    def __init__(self) -> None:
        self._future: asyncio.Future[ValueT] | None = None

    def _bound(self) -> asyncio.Future[ValueT]:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
        return self._future

    @property
    def done(self) -> bool:
        """Whether this generation has reached any terminal outcome."""
        return self._future is not None and self._future.done()

    @property
    def cancelled(self) -> bool:
        """Whether its owner cancelled before resolving the generation."""
        return self._future is not None and self._future.cancelled()

    def resolve(self, value: ValueT) -> None:
        """Publish a successful outcome once."""
        future = self._bound()
        if not future.done():
            future.set_result(value)

    def cancel(self) -> None:
        """Wake joiners by cancelling this generation's completion signal."""
        future = self._bound()
        if not future.done():
            future.cancel()

    async def wait(self) -> ValueT:
        """Wait without letting a cancelled joiner cancel every other observer."""
        return await asyncio.shield(self._bound())


__all__ = ["Completion"]
