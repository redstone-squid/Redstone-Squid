"""Framework-neutral message tracking service."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.db.schema import Message, MessagePurposeLiteral


@dataclass(frozen=True, slots=True)
class TrackedMessage:
    """Discord message metadata needed for persistence."""

    id: int
    server_id: int
    channel_id: int
    author_id: int
    content: str | None


class MessageRepository(Protocol):
    """Persistence operations required by :class:`MessageService`."""

    async def insert(
        self,
        message_id: int,
        server_id: int,
        channel_id: int,
        author_id: int,
        purpose: MessagePurposeLiteral,
        content: str | None,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None: ...

    async def update_edited_time(self, message_id: int) -> None: ...

    async def get_by_id(self, message_id: int) -> Message | None: ...

    async def delete_by_id(self, message_id: int) -> Message: ...

    async def get_outdated_messages(self, server_id: int) -> Sequence[Message]: ...


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
            raise ValueError(msg)
        if purpose == "vote" and vote_session_id is None:
            msg = "vote_session_id cannot be None for this purpose."
            raise ValueError(msg)
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

    async def untrack(self, message_id: int) -> Message:
        return await self._repository.delete_by_id(message_id)

    async def get(self, message_id: int) -> Message | None:
        return await self._repository.get_by_id(message_id)

    async def get_outdated(self, server_id: int) -> Sequence[Message]:
        return await self._repository.get_outdated_messages(server_id)
