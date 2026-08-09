"""Tracked message application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.messages.domain import MessagePurposeLiteral, MessageRecord


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

    async def get_by_id(self, message_id: int) -> MessageRecord | None: ...

    async def delete_by_id(self, message_id: int) -> MessageRecord: ...

    async def list_for_build(self, build_id: int, author_id: int) -> Sequence[MessageRecord]: ...

    async def list_for_build_purpose(
        self, build_id: int, purpose: MessagePurposeLiteral
    ) -> Sequence[MessageRecord]: ...
