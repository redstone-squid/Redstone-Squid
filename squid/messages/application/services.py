"""Discord message application services."""

from whenever import Instant

from squid.messages.application.ports import MessageRepository
from squid.messages.domain import MessageFact, MessageRecord


class MessageService:
    """Record what is true about the Discord messages the bot has seen.

    Only facts. What a message is *for* is expressed by whatever links to it —
    `discord_posts` for the ones the bot owns and renders, `build_source_messages` for
    the ones a build was inferred from — rather than by a purpose stored on the row.
    """

    def __init__(self, repository: MessageRepository):
        self._repository = repository

    async def observe(self, fact: MessageFact) -> None:
        """Record a Discord message as a fact, refreshing it if already known.

        Idempotent by design: the same message legitimately arrives from several
        directions, and each is recording the same row rather than competing for it.
        """
        await self._repository.upsert_fact(fact)

    async def record_edit(self, message_id: int, content: str | None) -> bool:
        """Refresh stored content after Discord reports an edit.

        Returns whether a stored message matched; most edits are to messages the
        bot has no reason to know about.
        """
        return await self._repository.record_edit(message_id, content, Instant.now())

    async def mark_deleted(self, message_id: int) -> bool:
        """Tombstone a message Discord reports gone, retaining it as a fact.

        Returns whether a stored message matched.
        """
        return await self._repository.mark_deleted(message_id, Instant.now())

    async def get(self, message_id: int) -> MessageRecord | None:
        return await self._repository.get_by_id(message_id)
