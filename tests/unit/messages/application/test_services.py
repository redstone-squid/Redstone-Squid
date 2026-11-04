"""Tracked message application service tests."""

from dataclasses import replace
from functools import partial

from whenever import Instant

from squid.messages.application import MessageService
from squid.messages.domain import MessageFact
from squid.messages.models import MessageRecord
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


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

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        fact = self.facts.get(message_id)
        if fact is None:
            return None
        return MessageRecord(
            id=fact.id,
            channel_id=fact.channel_id,
            author_id=fact.author_id,
            guild_id=fact.guild_id,
            content=fact.content,
            deleted_at=self.deleted_at.get(message_id),
        )

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        if message_id not in self.facts or message_id in self.deleted_at:
            return False
        self.deleted_at[message_id] = deleted_at
        return True


class FakeMessageUnitOfWork(FakeUnitOfWork[FakeMessageRepository]):
    @property
    def messages(self) -> FakeMessageRepository:
        return self.state


def _fact(message_id: int = 1, *, content: str | None = "hello") -> MessageFact:
    return MessageFact(id=message_id, channel_id=20, author_id=30, guild_id=10, content=content)


async def test_observing_the_same_message_twice_keeps_one_fact() -> None:
    """A second observation refreshes content without changing the first-seen time."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))

    await service.observe(_fact())
    first_seen = committed.value.observed_at[1]
    await service.observe(_fact(content="hello (edited elsewhere)"))

    assert list(committed.value.facts) == [1]
    assert committed.value.facts[1].content == "hello (edited elsewhere)"
    # Re-observing must not rewrite when the bot first saw the message.
    assert committed.value.observed_at[1] == first_seen


async def test_observing_a_dm_is_allowed() -> None:
    """Direct messages have no guild ID."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))

    await service.observe(MessageFact(id=5, channel_id=20, author_id=30, guild_id=None, content="dm"))

    assert committed.value.facts[5].guild_id is None


async def test_edits_and_deletes_report_whether_the_message_was_known() -> None:
    """Unknown messages and repeated deletions return False."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))
    await service.observe(_fact())

    assert await service.record_edit(1, "changed") is True
    assert committed.value.facts[1].content == "changed"
    assert await service.record_edit(999, "changed") is False

    assert await service.mark_deleted(1) is True
    # A redelivered raw event must not move the tombstone forward.
    assert await service.mark_deleted(1) is False
    assert await service.mark_deleted(999) is False
