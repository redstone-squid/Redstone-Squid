"""Tracked message application service tests."""

from dataclasses import replace
from functools import partial

from whenever import Instant

from squid.messages.application import MessageService
from squid.messages.domain import MessageSnapshot
from squid.messages.models import Message
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeMessageRepository:
    """An in-memory stand-in that keeps enough state to observe upsert semantics."""

    def __init__(self) -> None:
        self.snapshots: dict[int, MessageSnapshot] = {}
        self.observed_at: dict[int, Instant] = {}
        self.edited_at: dict[int, Instant] = {}
        self.deleted_at: dict[int, Instant] = {}

    async def upsert_snapshot(self, snapshot: MessageSnapshot) -> None:
        self.snapshots[snapshot.id] = snapshot
        self.observed_at.setdefault(snapshot.id, Instant.now())

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        if message_id not in self.snapshots:
            return False
        self.snapshots[message_id] = replace(self.snapshots[message_id], content=content)
        self.edited_at[message_id] = edited_at
        return True

    async def refresh_content(self, message_id: int, content: str | None, edited_at: Instant | None) -> bool:
        if message_id not in self.snapshots or self.snapshots[message_id].content == content:
            return False
        self.snapshots[message_id] = replace(self.snapshots[message_id], content=content)
        if edited_at is not None:
            self.edited_at[message_id] = edited_at
        return True

    async def get_by_id(self, message_id: int) -> Message | None:
        snapshot = self.snapshots.get(message_id)
        if snapshot is None:
            return None
        assert snapshot.author_id is not None
        return Message(
            id=snapshot.id,
            channel_id=snapshot.channel_id,
            author_id=snapshot.author_id,
            guild_id=snapshot.guild_id,
            content=snapshot.content,
            deleted_at=self.deleted_at.get(message_id),
        )

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        if message_id not in self.snapshots or message_id in self.deleted_at:
            return False
        self.deleted_at[message_id] = deleted_at
        return True


class FakeMessageUnitOfWork(FakeUnitOfWork[FakeMessageRepository]):
    @property
    def messages(self) -> FakeMessageRepository:
        return self.state


def _snapshot(message_id: int = 1, *, content: str | None = "hello") -> MessageSnapshot:
    return MessageSnapshot(id=message_id, channel_id=20, author_id=30, guild_id=10, content=content)


async def test_observing_the_same_message_twice_keeps_one_snapshot() -> None:
    """A second observation refreshes content without changing the first-seen time."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))

    await service.observe(_snapshot())
    first_seen = committed.value.observed_at[1]
    await service.observe(_snapshot(content="hello (edited elsewhere)"))

    assert list(committed.value.snapshots) == [1]
    assert committed.value.snapshots[1].content == "hello (edited elsewhere)"
    # Re-observing must not rewrite when the bot first saw the message.
    assert committed.value.observed_at[1] == first_seen


async def test_observing_a_dm_is_allowed() -> None:
    """Direct messages have no guild ID."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))

    await service.observe(MessageSnapshot(id=5, channel_id=20, author_id=30, guild_id=None, content="dm"))

    assert committed.value.snapshots[5].guild_id is None


async def test_edits_and_deletes_report_whether_the_message_was_known() -> None:
    """Unknown messages and repeated deletions return False."""
    committed = CommittedState(FakeMessageRepository())
    service = MessageService(uow_factory=partial(FakeMessageUnitOfWork, committed))
    await service.observe(_snapshot())

    assert await service.record_edit(1, "changed") is True
    assert committed.value.snapshots[1].content == "changed"
    assert await service.record_edit(999, "changed") is False

    assert await service.mark_deleted(1) is True
    # A redelivered raw event must not move the tombstone forward.
    assert await service.mark_deleted(1) is False
    assert await service.mark_deleted(999) is False
