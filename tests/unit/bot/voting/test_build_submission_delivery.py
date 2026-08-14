# pyright: reportPrivateUsage=false
"""Retry behavior for Discord build-review cards."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from squid.bot.voting.build_session import BuildVoteSession, _review_message_nonce
from squid.builds.domain import OtherBuild, Status
from squid.voting.domain import EmojiPreset, VoteChoice, VoteOption, VoteSessionSnapshot, VoteTarget


@dataclass(frozen=True)
class _Guild:
    id: int


@dataclass(frozen=True)
class _Channel:
    id: int
    guild: _Guild


def _session(existing_channels: dict[int, int]) -> BuildVoteSession:
    session = BuildVoteSession.__new__(BuildVoteSession)
    session.id = 12
    session._message_channels = existing_channels
    session.options = ()
    session.send_message = AsyncMock()  # type: ignore[method-assign]
    session.update_messages = AsyncMock()  # type: ignore[method-assign]
    session.fetch_messages = AsyncMock(return_value=set())  # type: ignore[method-assign]
    return session


async def test_retry_posts_only_channels_without_a_tracked_review_card() -> None:
    session = _session({100: 1})
    first = _Channel(1, _Guild(10))
    missing = _Channel(2, _Guild(20))

    await session._post_missing_messages([first, missing])  # type: ignore[arg-type]

    session.send_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        missing,
        nonce=_review_message_nonce(12, 2),
    )
    session.update_messages.assert_awaited_once_with()  # type: ignore[attr-defined]


async def test_partial_send_failure_settles_all_channels_before_retrying_event() -> None:
    session = _session({})
    attempted: list[int] = []

    async def send(channel: _Channel, *, nonce: int) -> Any:
        del nonce
        attempted.append(channel.id)
        if channel.id == 1:
            msg = "discord is down"
            raise RuntimeError(msg)
        return object()

    session.send_message = send  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="discord is down"):
        await session._post_missing_messages(  # type: ignore[arg-type]
            [_Channel(1, _Guild(10)), _Channel(2, _Guild(20))]  # type: ignore[arg-type]
        )

    assert sorted(attempted) == [1, 2]
    session.update_messages.assert_not_awaited()  # type: ignore[attr-defined]


def test_review_message_nonce_is_stable_and_scoped_to_the_channel() -> None:
    assert _review_message_nonce(12, 34) == _review_message_nonce(12, 34)
    assert _review_message_nonce(12, 34) != _review_message_nonce(12, 35)


async def test_retry_after_send_succeeds_but_tracking_fails_reuses_discord_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _Guild(10)
    options = (
        VoteOption("👍", VoteChoice.APPROVE, guild_id=guild.id),
        VoteOption("👎", VoteChoice.DENY, guild_id=guild.id),
    )
    snapshot = VoteSessionSnapshot(
        id=12,
        author_account_id=7,
        kind="build",
        status="open",
        result="pending",
        pass_threshold=3,
        fail_threshold=-3,
        votes={},
        messages=(),
        options=options,
        target=VoteTarget(build_id=42),
    )

    class _Message:
        def __init__(self, channel: "_DeduplicatingChannel") -> None:
            self.id = 100
            self.guild = guild
            self.channel = channel
            self.author = SimpleNamespace(id=999)
            self.content = ""

        async def add_reaction(self, _emoji: str) -> None:
            return None

    class _DeduplicatingChannel:
        id = 20

        def __init__(self) -> None:
            self.guild = guild
            self.attempts = 0
            self.created: dict[int, Any] = {}

        async def send(self, *, view: object, allowed_mentions: object, nonce: int) -> Any:
            del view, allowed_mentions
            self.attempts += 1
            if nonce not in self.created:
                self.created[nonce] = _Message(self)
            return self.created[nonce]

    class _Messages:
        def __init__(self) -> None:
            self.attempts = 0

        async def track(self, *_args: object, **_kwargs: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                msg = "database commit failed"
                raise RuntimeError(msg)

    votes = AsyncMock()
    votes.emoji_preset.return_value = EmojiPreset(guild.id, "build", options)
    votes.ensure_build_submission_vote.return_value = snapshot.id
    votes.get_session_by_id.return_value = snapshot
    messages = _Messages()
    bot = SimpleNamespace(
        services=SimpleNamespace(votes=votes, messages=messages),
        for_build=lambda _build: SimpleNamespace(render_layout=AsyncMock(return_value=object())),
    )
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    channel = _DeduplicatingChannel()

    async def skip_render(_self: BuildVoteSession) -> None:
        return None

    monkeypatch.setattr(BuildVoteSession, "update_messages", skip_render)

    with pytest.raises(RuntimeError, match="database commit failed"):
        await BuildVoteSession.ensure_submission(bot, build, [channel])  # type: ignore[arg-type]
    await BuildVoteSession.ensure_submission(bot, build, [channel])  # type: ignore[arg-type]

    assert channel.attempts == 2
    assert len(channel.created) == 1
    assert messages.attempts == 2
    assert votes.ensure_build_submission_vote.await_count == 2
