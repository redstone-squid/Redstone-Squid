"""Discord message application ports."""

from typing import Protocol

from whenever import Instant

from squid.messages.domain import MessageFact, MessageRecord


class MessageRepository(Protocol):
    """Persistence operations required by :class:`MessageService`."""

    async def upsert_fact(self, fact: MessageFact) -> None: ...

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool: ...

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool: ...

    async def get_by_id(self, message_id: int) -> MessageRecord | None: ...
