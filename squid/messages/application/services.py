"""Tracked message application services."""

from collections.abc import Sequence

from whenever import Instant

from squid.messages.application.ports import MessageRepository
from squid.messages.domain import (
    MessageFact,
    MessagePurposeLiteral,
    MessageRecord,
    ProjectionResourceKind,
    TrackedMessage,
)
from squid.messages.errors import InvalidMessageError


class MessageService:
    """Record what is true about Discord messages, and what they are used for."""

    def __init__(self, repository: MessageRepository):
        self._repository = repository

    async def observe(self, fact: MessageFact) -> None:
        """Record a Discord message as a fact, refreshing it if already known.

        Idempotent by design: the same message legitimately arrives from several
        directions (a build's provenance, a starboard origin, a vote target), and
        each is recording the same row rather than competing for it.
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

    async def track(
        self,
        message: TrackedMessage,
        purpose: MessagePurposeLiteral,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None:
        # "confirm_pending_build" used to be listed here, but it is not a member of
        # MessagePurposeLiteral, so "view_confirmed_build" went unvalidated instead.
        if purpose in ("view_pending_build", "view_confirmed_build") and build_id is None:
            msg = "build_id cannot be None for this purpose."
            raise InvalidMessageError(msg, context={"purpose": purpose})
        if purpose == "vote" and vote_session_id is None:
            msg = "vote_session_id cannot be None for this purpose."
            raise InvalidMessageError(msg, context={"purpose": purpose})
        await self._repository.insert(
            message_id=message.id,
            server_id=message.server_id,
            channel_id=message.channel_id,
            author_id=message.author_id,
            purpose=purpose,
            content=message.content,
            build_id=build_id,
            vote_session_id=vote_session_id,
        )

    async def update_edited_time(self, message_id: int) -> None:
        await self._repository.update_edited_time(message_id)

    async def untrack(self, message_id: int) -> MessageRecord:
        return await self._repository.delete_by_id(message_id)

    async def get(self, message_id: int) -> MessageRecord | None:
        return await self._repository.get_by_id(message_id)

    async def list_for_build(self, build_id: int, author_id: int) -> Sequence[MessageRecord]:
        """Return messages for a build created by one Discord author."""
        return await self._repository.list_for_build(build_id, author_id)

    async def list_for_build_purpose(self, build_id: int, purpose: MessagePurposeLiteral) -> Sequence[MessageRecord]:
        """Return every tracked message serving one purpose for a build.

        Unlike :meth:`list_for_build` this is not scoped to an author, because callers
        use it to decide whether a message has already been posted at all.
        """
        return await self._repository.list_for_build_purpose(build_id, purpose)

    async def list_projection(self, resource_kind: ProjectionResourceKind, source_key: str) -> Sequence[MessageRecord]:
        """Return actual Discord messages belonging to one desired projection."""
        return await self._repository.list_projection(resource_kind, source_key)

    async def mark_projection_applied(
        self,
        resource_kind: ProjectionResourceKind,
        source_key: str,
        generation: int,
    ) -> None:
        """Acknowledge only messages still targeting the generation just rendered."""
        await self._repository.mark_projection_applied(resource_kind, source_key, generation)
