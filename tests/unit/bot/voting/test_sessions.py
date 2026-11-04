"""Review-session creation from the Discord side."""

import logging
from typing import Any, cast

import pytest

from squid.bot.voting.sessions import ensure_build_review
from squid.builds.domain import Status
from squid.builds.models import OtherBuild


async def test_a_guild_without_a_vote_channel_does_not_fail_the_submission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The build is already committed; missing vote channels must not fail its event handler.

    Raising here would report a saved build as failed and keep retrying the same delivery.
    """
    build = OtherBuild(id=42, submission_status=Status.PENDING, submitter_account_id=7)

    with caplog.at_level(logging.WARNING, logger="squid.bot.voting.sessions"):
        session_id = await ensure_build_review(cast(Any, None), build, [])

    assert session_id is None
    assert "no vote card" in caplog.text
