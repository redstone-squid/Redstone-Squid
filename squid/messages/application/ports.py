"""Discord message application ports."""

from typing import Protocol

from whenever import Instant

from squid.messages.domain import MessageSnapshot
from squid.messages.models import Message
from squid.unit_of_work import UnitOfWork


class MessageRepository(Protocol):
    """Persistence operations required by :class:`MessageService`."""

    async def upsert_snapshot(self, snapshot: MessageSnapshot) -> None: ...

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool: ...

    async def refresh_content(self, message_id: int, content: str | None, edited_at: Instant | None) -> bool: ...

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool: ...

    async def get_by_id(self, message_id: int) -> Message | None: ...


class MessageUnitOfWork(UnitOfWork, Protocol):
    """Transaction and repositories used by MessageService."""

    @property
    def messages(self) -> MessageRepository: ...
