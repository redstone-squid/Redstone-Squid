"""Poll publication recovery contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import discord
import pytest

from squid.bot.voting.publisher import DiscordPollPublisher, PollPublication, PollPublicationError
from squid.messages.domain import MessageFact
from squid.voting.domain import PollScope, VoteOption, VoteVisibility
from tests.support.voting import GENERIC_OPTIONS


class VoteRecorder:
    def __init__(self, *, attachment_failures: int = 0) -> None:
        self.attachment_failures = attachment_failures
        self.create_calls = 0
        self.attach_calls: list[tuple[int, int]] = []

    async def create_generic_poll(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: list[VoteOption] | tuple[VoteOption, ...],
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
    ) -> int:
        del author_account_id, question, visibility, duration_seconds, options, guild_id, scope
        self.create_calls += 1
        return 41

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        self.attach_calls.append((vote_session_id, message_id))
        if len(self.attach_calls) <= self.attachment_failures:
            raise RuntimeError("temporary database failure")


class MessageRecorder:
    def __init__(self) -> None:
        self.facts: list[MessageFact] = []

    async def observe(self, fact: MessageFact) -> None:
        self.facts.append(fact)


@dataclass(frozen=True)
class Services:
    votes: VoteRecorder
    messages: MessageRecorder


class Bot:
    def __init__(self, votes: VoteRecorder, messages: MessageRecorder) -> None:
        self.services = Services(votes, messages)
        self.refresh_calls: list[tuple[str, str]] = []

    async def refresh_posts(self, resource_kind: str, resource_key: str) -> None:
        self.refresh_calls.append((resource_kind, resource_key))


@dataclass(frozen=True)
class Snowflake:
    id: int


@dataclass(frozen=True)
class Channel:
    id: int
    guild: Snowflake


@dataclass(frozen=True)
class Message:
    id: int
    channel: Channel
    author: Snowflake
    guild: Snowflake
    content: str = "Publishing poll…"
    created_at: datetime = datetime(2026, 8, 30, tzinfo=UTC)
    jump_url: str = "https://discord.example/channels/10/20/30"


class PublisherRecorder(DiscordPollPublisher):
    def __init__(self, bot: Bot, message: Message | None = None, *, send_failure: Exception | None = None) -> None:
        super().__init__(cast(Any, bot))
        self.message = message
        self.send_failure = send_failure
        self.send_calls = 0

    async def _send_placeholder(self, channel: Any) -> discord.Message:
        del channel
        self.send_calls += 1
        if self.send_failure is not None:
            raise self.send_failure
        assert self.message is not None
        return cast(discord.Message, self.message)


async def _publish(publisher: DiscordPollPublisher, channel: Channel) -> PollPublication:
    return await publisher.create_and_publish(
        author_account_id=7,
        channel=cast(Any, channel),
        question="Which?",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        duration_seconds=3600,
        options=GENERIC_OPTIONS,
    )


async def test_live_publication_retries_attachment_without_recreating_or_resending() -> None:
    votes = VoteRecorder(attachment_failures=1)
    messages = MessageRecorder()
    bot = Bot(votes, messages)
    channel = Channel(20, Snowflake(10))
    message = Message(30, channel, Snowflake(40), channel.guild)
    publisher = PublisherRecorder(bot, message)

    completed = await _publish(publisher, channel)

    assert completed.vote_session_id == 41
    assert completed.message is message
    assert votes.create_calls == 1
    assert publisher.send_calls == 1
    assert votes.attach_calls == [(41, 30), (41, 30)]
    assert [fact.id for fact in messages.facts] == [30, 30]
    assert bot.refresh_calls == [("vote_session", "41")]


async def test_a_send_failure_preserves_the_session_for_a_later_send() -> None:
    votes = VoteRecorder()
    messages = MessageRecorder()
    bot = Bot(votes, messages)
    channel = Channel(20, Snowflake(10))
    publisher = PublisherRecorder(bot, send_failure=RuntimeError("Discord unavailable"))

    with pytest.raises(PollPublicationError) as raised:
        await _publish(publisher, channel)

    assert raised.value.pending.vote_session_id == 41
    assert raised.value.pending.message is None
    assert votes.create_calls == 1
    assert votes.attach_calls == []

    message = Message(31, channel, Snowflake(40), channel.guild)
    publisher.send_failure = None
    publisher.message = message
    completed = await publisher.resume(raised.value.pending, cast(Any, channel))

    assert completed.message is message
    assert votes.create_calls == 1
    assert publisher.send_calls == 3
    assert votes.attach_calls == [(41, 31)]


async def test_a_second_attachment_failure_logs_an_operator_actionable_pending_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    votes = VoteRecorder(attachment_failures=2)
    messages = MessageRecorder()
    bot = Bot(votes, messages)
    channel = Channel(20, Snowflake(10))
    message = Message(30, channel, Snowflake(40), channel.guild)
    publisher = PublisherRecorder(bot, message)

    with (
        caplog.at_level("ERROR", logger="squid.bot.voting.publisher"),
        pytest.raises(PollPublicationError) as raised,
    ):
        await _publish(publisher, channel)

    assert raised.value.pending.vote_session_id == 41
    assert raised.value.pending.message is message
    record = caplog.records[-1]
    assert record.message == "Poll publication remains incomplete after retry"
    assert vars(record)["squid.vote.session_id"] == 41
    assert vars(record)["squid.message.id"] == 30
    assert vars(record)["squid.channel.id"] == 20
    assert vars(record)["squid.guild.id"] == 10
