"""SQLAlchemy implementation of application-owned transactions."""

from types import TracebackType
from typing import Literal, Self

from anyio import CancelScope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import InvalidStateError
from squid.messages.infrastructure.repository import MessageRepository
from squid.posts.infrastructure.repository import PostRepository
from squid.settings.infrastructure.repository import SettingsRepository


class SqlAlchemyUnitOfWork:
    """Own one session and expose repositories that share its transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._state: Literal["new", "active", "finished"] = "new"
        self._messages = MessageRepository(self._active_session)
        self._posts = PostRepository(self._active_session)
        self._settings = SettingsRepository(self._active_session)

    @property
    def settings(self) -> SettingsRepository:
        self._active_session()
        return self._settings

    @property
    def messages(self) -> MessageRepository:
        self._active_session()
        return self._messages

    @property
    def posts(self) -> PostRepository:
        self._active_session()
        return self._posts

    def _active_session(self) -> AsyncSession:
        if self._state != "active" or self._session is None:
            message = "The unit of work is not active."
            raise InvalidStateError(message)
        return self._session

    async def __aenter__(self) -> Self:
        if self._state != "new":
            message = "A unit of work can only be entered once."
            raise InvalidStateError(message)
        self._session = self._sessions(expire_on_commit=False)
        try:
            await self._session.begin()
        except BaseException:
            await self._close()
            raise
        self._state = "active"
        return self

    async def commit(self) -> None:
        session = self._active_session()
        try:
            await session.commit()
        finally:
            self._state = "finished"

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        self._state = "finished"
        if self._session is not None:
            # Close rolls back unfinished work, including when the caller was cancelled.
            with CancelScope(shield=True):
                await self._session.close()
            self._session = None
