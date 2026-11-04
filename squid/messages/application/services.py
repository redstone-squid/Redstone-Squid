"""Discord message application services."""

from whenever import Instant

from squid.messages.application.ports import MessageUnitOfWork
from squid.messages.domain import MessageFact
from squid.messages.models import MessageRecord
from squid.unit_of_work import UnitOfWorkFactory


class MessageService:
    """Store observed Discord messages and their edits and deletions.

    Other features link to these records: discord_posts tracks messages the bot
    renders, and build_source_messages links builds to their source messages.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[MessageUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def observe(self, fact: MessageFact) -> None:
        """Insert a message or replace its observed details if it is already known.

        Keep the first observation time and any recorded edit or deletion times.
        A later observation can replace stored content with None.
        """
        async with self._uow_factory() as uow:
            await uow.messages.upsert_fact(fact)
            await uow.commit()

    async def record_edit(self, message_id: int, content: str | None) -> bool:
        """Refresh stored content after Discord reports an edit.

        Returns whether a stored message matched; most edits are to messages the
        bot has no reason to know about.
        """
        async with self._uow_factory() as uow:
            changed = await uow.messages.record_edit(message_id, content, Instant.now())
            await uow.commit()
            return changed

    async def mark_deleted(self, message_id: int) -> bool:
        """Record a message's deletion without removing its stored details.

        Return True on the first deletion of a known message. Return False for
        unknown messages and repeated deletion notices.
        """
        async with self._uow_factory() as uow:
            changed = await uow.messages.mark_deleted(message_id, Instant.now())
            await uow.commit()
            return changed

    async def get(self, message_id: int) -> MessageRecord | None:
        """Return a stored message, including deleted ones, or None if unknown."""
        async with self._uow_factory() as uow:
            return await uow.messages.get_by_id(message_id)
