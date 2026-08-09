"""Tracked message application services."""

from collections.abc import Sequence

from squid.messages.application.ports import MessageRepository
from squid.messages.domain import MessagePurposeLiteral, MessageRecord, TrackedMessage
from squid.messages.errors import InvalidMessageError


class MessageService:
    """Validate and persist tracked message metadata."""

    def __init__(self, repository: MessageRepository):
        self._repository = repository

    async def track(
        self,
        message: TrackedMessage,
        purpose: MessagePurposeLiteral,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None:
        if purpose in ("view_pending_build", "confirm_pending_build") and build_id is None:
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
