"""Common scope behavior for service test doubles."""

from types import TracebackType
from typing import Self

from squid.messages.application.ports import MessageRepository
from squid.posts.application.ports import PostRepository
from squid.settings.application.ports import SettingsStore
from squid.versions.application.ports import VersionRepository


class FakeUnitOfWork:
    """Let each service fake supply its repositories and commit snapshots."""

    @property
    def settings(self) -> SettingsStore:
        raise AssertionError("This test does not provide a settings repository.")

    @property
    def messages(self) -> MessageRepository:
        raise AssertionError("This test does not provide a message repository.")

    @property
    def posts(self) -> PostRepository:
        raise AssertionError("This test does not provide a post repository.")

    @property
    def versions(self) -> VersionRepository:
        raise AssertionError("This test does not provide a version repository.")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        pass

    async def commit(self) -> None:
        raise NotImplementedError
