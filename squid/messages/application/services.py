"""Discord message application services."""

from whenever import Instant

from squid.messages.application.ports import MessageUnitOfWork
from squid.messages.domain import MessageFact
from squid.messages.models import MessageRecord
from squid.unit_of_work import UnitOfWorkFactory


class MessageService:
    """Record what is true about the Discord messages the bot has seen.

    Only facts. What a message is *for* is expressed by whatever links to it —
    `discord_posts` for the ones the bot owns and renders, `build_source_messages` for
    the ones a build was inferred from — rather than by a purpose stored on the row.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[MessageUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def observe(self, fact: MessageFact) -> None:
        """Record a Discord message as a fact, refreshing it if already known.

        Idempotent by design: the same message legitimately arrives from several
        directions, and each is recording the same row rather than competing for it.
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
        """Tombstone a message Discord reports gone, retaining it as a fact.

        Returns whether a stored message matched.
        """
        async with self._uow_factory() as uow:
            changed = await uow.messages.mark_deleted(message_id, Instant.now())
            await uow.commit()
            return changed

    async def get(self, message_id: int) -> MessageRecord | None:
        async with self._uow_factory() as uow:
            return await uow.messages.get_by_id(message_id)
