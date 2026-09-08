"""Discord message application ports."""

from typing import Protocol

from whenever import Instant

from squid.messages.domain import MessageFact, MessageRecord


class MessageRepository(Protocol):
    """Persistence operations required by `MessageService`."""

    async def upsert_fact(self, fact: MessageFact) -> None:
        """Insert or refresh the one row for this message, keeping the `observed_at` of the first sighting."""
        ...

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        """Replace the stored content, returning whether the message was known."""
        ...

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        """Tombstone the message, returning whether an undeleted one matched; the first deletion time stands."""
        ...

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        """The stored fact, tombstoned or not, or `None` if the message was never recorded."""
        ...
