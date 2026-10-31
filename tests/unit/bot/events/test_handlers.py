"""Domain event handler tests.

Publishing a confirmed build's card is no longer a handler: confirming enqueues a
Discord sync job and the reconciler renders it, so those cases live in
`tests/unit/bot/posts/test_reconciler.py`.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from whenever import Instant

from squid.bot.events.handlers import PostSubmittedBuildHandler
from squid.builds.domain import OtherBuild, Status
from squid.events import DomainEvent, UnsupportedEventVersionError


def _event(build_id: int = 42, *, event_type: str = "build.confirmed", schema_version: int = 1) -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type=event_type,
        aggregate_kind="build",
        aggregate_id=build_id,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={"previous_status": 0, "status": 1},
        schema_version=schema_version,
    )


async def test_submitted_build_is_delegated_to_the_idempotent_review_publisher() -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    bot = AsyncMock()
    bot.services.build_queries.get.return_value = build
    publisher = AsyncMock()
    bot.for_build = Mock(return_value=publisher)

    await PostSubmittedBuildHandler(bot).handle(_event(event_type="build.submitted", schema_version=2))

    publisher.post_for_voting.assert_awaited_once_with()


async def test_submitted_build_redelivery_reuses_the_same_review_publisher_path() -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    bot = AsyncMock()
    bot.services.build_queries.get.return_value = build
    publisher = AsyncMock()
    bot.for_build = Mock(return_value=publisher)
    handler = PostSubmittedBuildHandler(bot)
    event = _event(event_type="build.submitted", schema_version=2)

    await handler.handle(event)
    await handler.handle(event)

    assert publisher.post_for_voting.await_count == 2


async def test_submitted_build_with_unsupported_schema_is_rejected() -> None:
    with pytest.raises(UnsupportedEventVersionError, match="schema version 99"):
        await PostSubmittedBuildHandler(AsyncMock()).handle(_event(event_type="build.submitted", schema_version=99))
