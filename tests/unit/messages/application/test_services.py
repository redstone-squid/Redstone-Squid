"""Tracked message application service tests."""

from collections.abc import Sequence
from dataclasses import replace

import pytest
from whenever import Instant

from squid.messages.application import MessageService
from squid.messages.domain import (
    MessageFact,
    MessagePurposeLiteral,
    MessageRecord,
    ProjectionResourceKind,
    TrackedMessage,
)
from squid.messages.errors import InvalidMessageError


class FakeMessageRepository:
    """An in-memory stand-in that keeps enough state to observe upsert semantics."""

    def __init__(self) -> None:
        self.facts: dict[int, MessageFact] = {}
        self.observed_at: dict[int, Instant] = {}
        self.edited_at: dict[int, Instant] = {}
        self.deleted_at: dict[int, Instant] = {}

    async def upsert_fact(self, fact: MessageFact) -> None:
        self.facts[fact.id] = fact
        self.observed_at.setdefault(fact.id, Instant.now())

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        if message_id not in self.facts:
            return False
        self.facts[message_id] = replace(self.facts[message_id], content=content)
        self.edited_at[message_id] = edited_at
        return True

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        if message_id not in self.facts or message_id in self.deleted_at:
            return False
        self.deleted_at[message_id] = deleted_at
        return True

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

    async def list_for_build_purpose(self, build_id: int, purpose: MessagePurposeLiteral) -> Sequence[MessageRecord]:
        return []

    async def list_projection(self, resource_kind: ProjectionResourceKind, source_key: str) -> Sequence[MessageRecord]:
        return []

    async def mark_projection_applied(
        self, resource_kind: ProjectionResourceKind, source_key: str, generation: int
    ) -> None:
        return None


def _fact(message_id: int = 1, *, content: str | None = "hello") -> MessageFact:
    return MessageFact(id=message_id, channel_id=20, author_id=30, guild_id=10, content=content)


async def test_observing_the_same_message_twice_keeps_one_fact() -> None:
    """Several uses of one message record the same row rather than competing for it."""
    repository = FakeMessageRepository()
    service = MessageService(repository)

    await service.observe(_fact())
    first_seen = repository.observed_at[1]
    await service.observe(_fact(content="hello (edited elsewhere)"))

    assert list(repository.facts) == [1]
    assert repository.facts[1].content == "hello (edited elsewhere)"
    # Re-observing must not rewrite when the bot first saw the message.
    assert repository.observed_at[1] == first_seen


async def test_observing_a_dm_is_allowed() -> None:
    """A message fact is true regardless of where it lives; only the guild is unknown."""
    repository = FakeMessageRepository()
    service = MessageService(repository)

    await service.observe(MessageFact(id=5, channel_id=20, author_id=30, guild_id=None, content="dm"))

    assert repository.facts[5].guild_id is None


async def test_edits_and_deletes_report_whether_the_message_was_known() -> None:
    """Most Discord edits and deletes are to messages the bot has no reason to store."""
    repository = FakeMessageRepository()
    service = MessageService(repository)
    await service.observe(_fact())

    assert await service.record_edit(1, "changed") is True
    assert repository.facts[1].content == "changed"
    assert await service.record_edit(999, "changed") is False

    assert await service.mark_deleted(1) is True
    # A redelivered raw event must not move the tombstone forward.
    assert await service.mark_deleted(1) is False
    assert await service.mark_deleted(999) is False


async def test_message_service_requires_vote_session_id() -> None:
    service = MessageService(FakeMessageRepository())
    message = TrackedMessage(1, 2, 3, 4, "content")

    with pytest.raises(InvalidMessageError, match="vote_session_id"):
        await service.track(message, "vote")


async def test_message_service_requires_build_id_for_build_views() -> None:
    """`view_confirmed_build` went unvalidated: the guard named a purpose that never existed."""
    service = MessageService(FakeMessageRepository())
    message = TrackedMessage(1, 2, 3, 4, "content")

    with pytest.raises(InvalidMessageError, match="build_id"):
        await service.track(message, "view_confirmed_build")
