"""PostConfirmedBuildHandler tests."""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest
from whenever import Instant

from squid.bot.events.handlers import PostConfirmedBuildHandler
from squid.builds.domain import Build, Status
from squid.events import DomainEvent
from squid.messages.domain import MessageRecord


@dataclass
class FakeChannel:
    id: int
    sent: list[tuple[object, object]] = field(default_factory=list)

    async def send(self, *, view: object, allowed_mentions: object) -> Any:
        self.sent.append((view, allowed_mentions))
        return _FakeMessage(id=1000 + self.id, channel=_FakeIdentified(self.id))


@dataclass
class _FakeIdentified:
    id: int


@dataclass
class _FakeMessage:
    id: int
    channel: _FakeIdentified
    guild: _FakeIdentified = field(default_factory=lambda: _FakeIdentified(500))
    author: _FakeIdentified = field(default_factory=lambda: _FakeIdentified(999))
    content: str = ""


@dataclass
class FakeBuildHandler:
    channels: list[FakeChannel]

    async def get_channels_to_post_to(self) -> list[FakeChannel]:
        return self.channels

    async def render_layout(self) -> object:
        return object()


class FakeMessages:
    def __init__(self, existing: list[MessageRecord] | None = None) -> None:
        self.existing = existing or []
        self.tracked: list[tuple[int, str]] = []

    async def list_for_build_purpose(self, build_id: int, purpose: str) -> list[MessageRecord]:
        return [r for r in self.existing if r.build_id == build_id and r.purpose == purpose]

    async def track(self, message: Any, purpose: str, *, build_id: int) -> None:
        self.tracked.append((message.channel_id, purpose))
        self.existing.append(
            MessageRecord(
                id=message.id,
                server_id=0,
                channel_id=message.channel_id,
                author_id=999,
                purpose=purpose,  # type: ignore[arg-type]
                content=None,
                build_id=build_id,
                vote_session_id=None,
                updated_at=Instant.now(),
            )
        )


def _bot(build: Build | None, channels: list[FakeChannel], messages: FakeMessages) -> Any:
    bot = AsyncMock()
    bot.services.build_queries.get = AsyncMock(return_value=build)
    bot.services.messages = messages
    bot.for_build = lambda _build: FakeBuildHandler(channels)
    return bot


def _event(build_id: int = 42) -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="build.confirmed",
        aggregate_kind="build",
        aggregate_id=build_id,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={"previous_status": 0, "status": 1},
    )


async def test_posts_a_confirmed_build_to_every_configured_channel() -> None:
    build = Build(id=42)
    build.submission_status = Status.CONFIRMED
    channels = [FakeChannel(id=1), FakeChannel(id=2)]
    messages = FakeMessages()

    await PostConfirmedBuildHandler(_bot(build, channels, messages)).handle(_event())

    assert [len(channel.sent) for channel in channels] == [1, 1]
    assert sorted(messages.tracked) == [(1, "view_confirmed_build"), (2, "view_confirmed_build")]


async def test_redelivery_does_not_post_the_build_again() -> None:
    """At-least-once delivery must not produce a second copy of the same post."""
    build = Build(id=42)
    build.submission_status = Status.CONFIRMED
    channels = [FakeChannel(id=1), FakeChannel(id=2)]
    messages = FakeMessages()
    handler = PostConfirmedBuildHandler(_bot(build, channels, messages))

    await handler.handle(_event())
    await handler.handle(_event())

    assert [len(channel.sent) for channel in channels] == [1, 1]


async def test_redelivery_after_a_partial_failure_posts_only_the_missing_channel() -> None:
    build = Build(id=42)
    build.submission_status = Status.CONFIRMED
    channels = [FakeChannel(id=1), FakeChannel(id=2)]
    messages = FakeMessages(
        existing=[
            MessageRecord(
                id=1001,
                server_id=0,
                channel_id=1,
                author_id=999,
                purpose="view_confirmed_build",
                content=None,
                build_id=42,
                vote_session_id=None,
                updated_at=Instant.now(),
            )
        ]
    )

    await PostConfirmedBuildHandler(_bot(build, channels, messages)).handle(_event())

    assert len(channels[0].sent) == 0
    assert len(channels[1].sent) == 1


async def test_a_build_no_longer_confirmed_is_not_posted() -> None:
    """A later transition owns its own event, so the stale one must do nothing."""
    build = Build(id=42)
    build.submission_status = Status.DENIED
    channels = [FakeChannel(id=1)]

    await PostConfirmedBuildHandler(_bot(build, channels, FakeMessages())).handle(_event())

    assert channels[0].sent == []


async def test_a_deleted_build_is_skipped_without_raising() -> None:
    channels = [FakeChannel(id=1)]
    await PostConfirmedBuildHandler(_bot(None, channels, FakeMessages())).handle(_event())
    assert channels[0].sent == []


async def test_a_failing_send_propagates_so_the_delivery_retries() -> None:
    build = Build(id=42)
    build.submission_status = Status.CONFIRMED

    class ExplodingChannel(FakeChannel):
        async def send(self, *, view: object, allowed_mentions: object) -> Any:
            msg = "discord is down"
            raise RuntimeError(msg)

    channels: list[FakeChannel] = [ExplodingChannel(id=1)]
    with pytest.raises(RuntimeError, match="discord is down"):
        await PostConfirmedBuildHandler(_bot(build, channels, FakeMessages())).handle(_event())
