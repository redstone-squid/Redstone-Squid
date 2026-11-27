"""Review-session creation from the Discord side."""

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

from squid.bot.voting.sessions import ensure_build_review
from squid.core.actors import ActingContext
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import OtherBuild
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.voting.domain import DEFAULT_VOTE_MENU, VoteKind
from tests.support.settings import RuntimeSettingsStub

pytestmark = [
    pytest.mark.parametrize("discord_worker_context", ["events.discord.build.submitted"], indirect=True),
    pytest.mark.usefixtures("discord_worker_context"),
]


async def test_a_guild_without_a_vote_channel_does_not_fail_the_submission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The build is already committed; missing vote channels must not fail its event handler.

    Raising here would report a saved build as failed and keep retrying the same delivery.
    """
    build = OtherBuild(id=42, submission_status=BuildStatus.PENDING, submitter_account_id=7)

    with caplog.at_level(logging.WARNING, logger="squid.bot.voting.sessions"):
        session_id = await ensure_build_review(cast(Any, None), build, [])

    assert session_id is None
    assert "no vote card" in caplog.text


async def test_build_reviews_read_current_default_thresholds() -> None:
    settings = RuntimeSettingsStub(RuntimeSettings(build_vote_pass_threshold=5, build_vote_fail_threshold=-4))
    votes = SimpleNamespace(
        emoji_presets=AsyncMock(
            return_value={1: SimpleNamespace(menu=DEFAULT_VOTE_MENU), 2: SimpleNamespace(menu=DEFAULT_VOTE_MENU)}
        ),
        ensure_build_submission_vote=AsyncMock(return_value=123),
    )
    bot = SimpleNamespace(services=SimpleNamespace(settings=settings, votes=votes), refresh_posts=AsyncMock())
    build = OtherBuild(id=42, submission_status=BuildStatus.PENDING, submitter_account_id=7)
    channels = [cast(discord.TextChannel, SimpleNamespace(id=10, guild=SimpleNamespace(id=1)))]
    await ensure_build_review(cast(Any, bot), build, channels)
    assert votes.ensure_build_submission_vote.call_args.kwargs["pass_threshold"] == 5
    assert votes.ensure_build_submission_vote.call_args.kwargs["fail_threshold"] == -4
    settings.value = RuntimeSettings(build_vote_pass_threshold=7)
    await ensure_build_review(cast(Any, bot), build, channels)
    assert votes.ensure_build_submission_vote.call_args.kwargs["pass_threshold"] == 7


async def test_review_batches_presets_for_unique_destination_guilds() -> None:
    votes = SimpleNamespace(
        emoji_presets=AsyncMock(return_value={}),
        ensure_build_submission_vote=AsyncMock(return_value=123),
    )
    bot = SimpleNamespace(
        services=SimpleNamespace(settings=RuntimeSettingsStub(), votes=votes), refresh_posts=AsyncMock()
    )
    build = OtherBuild(id=42, submission_status=BuildStatus.PENDING, submitter_account_id=7)
    channels = [
        cast(discord.TextChannel, SimpleNamespace(id=id_, guild=SimpleNamespace(id=guild)))
        for id_, guild in [(10, 1), (10, 1), (11, 1), (20, 2)]
    ]
    assert await ensure_build_review(cast(Any, bot), build, channels) == 123
    votes.emoji_presets.assert_awaited_once_with((1, 2), VoteKind.BUILD, context=ActingContext.current())
