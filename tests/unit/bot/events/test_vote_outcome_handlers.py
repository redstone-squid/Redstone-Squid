"""Handlers applying a closed vote session's outcome."""

from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from whenever import Instant

from squid.bot.events.handlers import DeleteVotedMessageHandler
from squid.builds.application import BuildService
from squid.events import DomainEvent
from squid.voting.application import VoteService
from squid.voting.domain import (
    BuildVoteTarget,
    DeleteLogVoteTarget,
    VoteKind,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteTarget,
)
from squid.voting.errors import InvalidVoteConfigurationError
from squid.worker.events import ApplyBuildVoteOutcomeHandler
from squid_ui_discord.testing import AsyncCallRecorder, MessageHarness
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services
from tests.support.voting import build_snapshot

DELETE_TARGET = DeleteLogVoteTarget(message_id=7, channel_id=8, server_id=9)


def _snapshot(
    *,
    kind: VoteKind,
    status: VoteStatus = VoteStatus.CLOSED,
    result: VoteSessionResult = VoteSessionResult.APPROVED,
    target: VoteTarget = None,
) -> VoteSessionSnapshot:
    return build_snapshot(id=5, author_account_id=1, kind=kind, status=status, result=result, target=target)


def _event() -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=5,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={"kind": "build", "result": "approved"},
    )


class VotesRecorder(VoteService):
    def __init__(self, snapshot: VoteSessionSnapshot | None) -> None:
        self.get_session = AsyncCallRecorder(result=snapshot)


class BuildsRecorder(BuildService):
    def __init__(self) -> None:
        self.confirm = AsyncCallRecorder()
        self.deny = AsyncCallRecorder()


def _bot(snapshot: VoteSessionSnapshot | None) -> Any:
    return make_layout_bot(services=bot_services(votes=VotesRecorder(snapshot), builds=BuildsRecorder()))


@pytest.mark.parametrize(
    ("result", "expected"),
    [(VoteSessionResult.APPROVED, "confirm"), (VoteSessionResult.DENIED, "deny")],
)
async def test_a_decided_build_vote_applies_its_outcome(result: VoteSessionResult, expected: str) -> None:
    bot = _bot(_snapshot(kind=VoteKind.BUILD, result=result, target=BuildVoteTarget(42)))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    getattr(bot.services.builds, expected).assert_awaited_once_with(42)


@pytest.mark.parametrize("result", [VoteSessionResult.CANCELLED, VoteSessionResult.PENDING])
async def test_an_undecided_build_vote_leaves_the_build_alone(result: VoteSessionResult) -> None:
    bot = _bot(_snapshot(kind=VoteKind.BUILD, result=result, target=BuildVoteTarget(42)))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()
    bot.services.builds.deny.assert_not_awaited()


async def test_a_still_open_session_is_not_applied() -> None:
    bot = _bot(
        _snapshot(
            kind=VoteKind.BUILD,
            status=VoteStatus.OPEN,
            result=VoteSessionResult.PENDING,
            target=BuildVoteTarget(42),
        )
    )

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()


def test_a_build_vote_without_a_target_is_rejected_by_the_domain() -> None:
    with pytest.raises(InvalidVoteConfigurationError, match="wrong target payload"):
        _snapshot(kind=VoteKind.BUILD, target=None)


async def test_a_delete_log_session_does_not_touch_builds() -> None:
    bot = _bot(_snapshot(kind=VoteKind.DELETE_LOG, target=DELETE_TARGET))

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()


async def test_an_approved_delete_log_vote_deletes_its_target() -> None:
    message = MessageHarness()
    bot = _bot(_snapshot(kind=VoteKind.DELETE_LOG, target=DELETE_TARGET))
    bot.get_or_fetch_message = AsyncCallRecorder(result=message)

    await DeleteVotedMessageHandler(bot).handle(_event())

    message.delete.assert_awaited_once()
    bot.get_or_fetch_message.assert_awaited_once_with(8, 7)


async def test_an_already_deleted_target_is_not_an_error() -> None:
    """Redelivery finds the message gone, which is the expected state."""
    message = MessageHarness()
    message.delete.side_effect = discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="gone")), "gone")
    bot = _bot(_snapshot(kind=VoteKind.DELETE_LOG, target=DELETE_TARGET))
    bot.get_or_fetch_message = AsyncCallRecorder(result=message)

    await DeleteVotedMessageHandler(bot).handle(_event())


async def test_a_denied_delete_log_vote_deletes_nothing() -> None:
    bot = _bot(_snapshot(kind=VoteKind.DELETE_LOG, result=VoteSessionResult.DENIED, target=DELETE_TARGET))
    bot.get_or_fetch_message = AsyncCallRecorder()

    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.get_or_fetch_message.assert_not_awaited()


async def test_a_build_session_is_not_deleted_by_the_delete_handler() -> None:
    bot = _bot(_snapshot(kind=VoteKind.BUILD, target=BuildVoteTarget(42)))
    bot.get_or_fetch_message = AsyncCallRecorder()

    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.get_or_fetch_message.assert_not_awaited()


async def test_a_missing_session_is_skipped_by_both_handlers() -> None:
    bot = _bot(None)
    bot.get_or_fetch_message = AsyncCallRecorder()

    await ApplyBuildVoteOutcomeHandler(bot.services.votes, bot.services.builds).handle(_event())
    await DeleteVotedMessageHandler(bot).handle(_event())

    bot.services.builds.confirm.assert_not_awaited()
    bot.get_or_fetch_message.assert_not_awaited()
