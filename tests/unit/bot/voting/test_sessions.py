"""Review-session creation from the Discord side."""

import logging
from typing import Any, cast

import pytest

from squid.bot.voting.sessions import ensure_build_review
from squid.builds.domain import OtherBuild, Status


async def test_a_guild_without_a_vote_channel_does_not_fail_the_submission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The build is already committed by the time we get here, so nowhere to post is not an error.

    It used to raise, which reported a saved build as a failed submission and made the
    at-least-once event handler retry it forever.
    """
    build = OtherBuild(id=42, submission_status=Status.PENDING, submitter_account_id=7)

    with caplog.at_level(logging.WARNING, logger="squid.bot.voting.sessions"):
        session_id = await ensure_build_review(cast(Any, None), build, [])

    assert session_id is None
    assert "no vote card" in caplog.text
