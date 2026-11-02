"""Application-owned transactions and their bound repositories."""

from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from squid.messages.application.ports import MessageRepository
    from squid.posts.application.ports import PostRepository
    from squid.settings.application.ports import SettingsStore
    from squid.versions.application.ports import VersionRepository


class UnitOfWork(Protocol):
    """Track mapped changes until commit; leaving without commit rolls back.

    Each scope belongs to one task. Commit is terminal, and repositories may only
    be used inside an active scope. Successfully committed entities are detached
    when the scope closes; entities from a rolled-back scope should be discarded.
    """

    @property
    def settings(self) -> SettingsStore: ...

    @property
    def messages(self) -> MessageRepository: ...

    @property
    def posts(self) -> PostRepository: ...

    @property
    def versions(self) -> VersionRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...

    async def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
