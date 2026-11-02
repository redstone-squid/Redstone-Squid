"""Common scope behavior for service test doubles."""

from types import TracebackType
from typing import Self


class FakeUnitOfWork:
    """Let each service fake supply its repositories and commit snapshots."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        pass

    async def commit(self) -> None:
        raise NotImplementedError
