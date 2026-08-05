"""Tracked message application service tests."""

from collections.abc import Sequence

import pytest

from squid.messages.application import MessageService
from squid.messages.domain import MessagePurposeLiteral, MessageRecord, TrackedMessage
from squid.messages.errors import InvalidMessageError


class FakeMessageRepository:
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
    ) -> None:
        return None

    async def update_edited_time(self, message_id: int) -> None:
        return None

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        return None

    async def delete_by_id(self, message_id: int) -> MessageRecord:
        msg = "not implemented by this test fake"
        raise LookupError(msg)

    async def list_for_build(self, build_id: int, author_id: int) -> Sequence[MessageRecord]:
        return []


async def test_message_service_requires_vote_session_id() -> None:
    service = MessageService(FakeMessageRepository())
    message = TrackedMessage(1, 2, 3, 4, "content")

    with pytest.raises(InvalidMessageError, match="vote_session_id"):
        await service.track(message, "vote")
