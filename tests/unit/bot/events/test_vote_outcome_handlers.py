"""Handlers applying a closed vote session's outcome."""

from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest
from whenever import Instant

from squid.bot.events.handlers import DeleteVotedMessageHandler
from squid.events import DomainEvent
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    VoteSessionResultLiteral,
    VoteSessionSnapshot,
    VoteTarget,
)
from squid.worker.events import ApplyBuildVoteOutcomeHandler


def _snapshot(
    *,
    kind: str,
    status: str = "closed",
    result: VoteSessionResultLiteral = "approved",
    target: VoteTarget | None = None,
) -> VoteSessionSnapshot:
    return VoteSessionSnapshot(
        id=5,
        author_id=1,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        result=result,
        pass_threshold=3,
        fail_threshold=-3,
        votes={},
        messages=(),
        options=tuple(DEFAULT_VOTE_OPTIONS),
        target=target or VoteTarget(),
    )


def _event() -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=5,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={"kind": "build", "result": "approved"},
    )


def _bot(snapshot: VoteSessionSnapshot | None) -> Any:
    bot = AsyncMock()
    bot.services.votes.get_session_by_id = AsyncMock(return_value=snapshot)
    return bot


@pytest.mark.parametrize(
    ("result", "expected"),
    [("approved", "confirm"), ("denied", "deny")],
)
async def test_a_decided_build_vote_applies_its_outcome(result: str, expected: str) -> None:
    bot = _bot(_snapshot(kind="build", result=result, target=VoteTarget(build_id=42)))  # type: ignore[arg-type]

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    getattr(bot.services.builds, expected).assert_awaited_once_with(42)


@pytest.mark.parametrize("result", ["cancelled", "pending"])
async def test_an_undecided_build_vote_leaves_the_build_alone(result: str) -> None:
    bot = _bot(_snapshot(kind="build", result=result, target=VoteTarget(build_id=42)))  # type: ignore[arg-type]

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()
    bot.services.builds.deny.assert_not_awaited()


async def test_a_still_open_session_is_not_applied() -> None:
    bot = _bot(_snapshot(kind="build", status="open", target=VoteTarget(build_id=42)))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()


async def test_a_build_vote_without_a_target_build_is_skipped() -> None:
    bot = _bot(_snapshot(kind="build", target=VoteTarget()))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()


async def test_a_delete_log_session_does_not_touch_builds() -> None:
    bot = _bot(_snapshot(kind="delete_log"))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()


async def test_an_approved_delete_log_vote_deletes_its_target() -> None:
    message = AsyncMock()
    bot = _bot(_snapshot(kind="delete_log", target=VoteTarget(message_id=7, channel_id=8)))
    bot.get_or_fetch_message = AsyncMock(return_value=message)

    await DeleteVotedMessageHandler(bot).handle(_event())

    message.delete.assert_awaited_once()
    bot.get_or_fetch_message.assert_awaited_once_with(8, 7, untrack_if_missing=False)


async def test_an_already_deleted_target_is_not_an_error() -> None:
    """Redelivery finds the message gone, which is the expected state."""
    message = AsyncMock()
    message.delete.side_effect = discord.NotFound(AsyncMock(status=404), "gone")
    bot = _bot(_snapshot(kind="delete_log", target=VoteTarget(message_id=7, channel_id=8)))
    bot.get_or_fetch_message = AsyncMock(return_value=message)

    await DeleteVotedMessageHandler(bot).handle(_event())


async def test_a_denied_delete_log_vote_deletes_nothing() -> None:
    bot = _bot(_snapshot(kind="delete_log", result="denied", target=VoteTarget(message_id=7, channel_id=8)))
    bot.get_or_fetch_message = AsyncMock()

    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.get_or_fetch_message.assert_not_awaited()


async def test_a_build_session_is_not_deleted_by_the_delete_handler() -> None:
    bot = _bot(_snapshot(kind="build", target=VoteTarget(build_id=42)))
    bot.get_or_fetch_message = AsyncMock()

    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.get_or_fetch_message.assert_not_awaited()


async def test_a_missing_session_is_skipped_by_both_handlers() -> None:
    bot = _bot(None)
    bot.get_or_fetch_message = AsyncMock()

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())
    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()
    bot.get_or_fetch_message.assert_not_awaited()
