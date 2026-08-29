"""Domain event handler tests.

Publishing a confirmed build's card is no longer a handler: confirming enqueues a
Discord sync job and the reconciler renders it, so those cases live in
`tests/unit/bot/posts/test_reconciler.py`.
"""

from dataclasses import dataclass
from typing import Any, cast, override

import pytest
from whenever import Instant

from squid.bot.events.handlers import PostSubmittedBuildHandler
from squid.builds.application import BuildQueryService
from squid.builds.domain import Build, OtherBuild, Status
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


class BuildQueryRecorder(BuildQueryService):
    def __init__(self, build: Build) -> None:
        self.build = build
        self.requested: list[int] = []

    @override
    async def get(self, build_id: int) -> Build | None:
        self.requested.append(build_id)
        return self.build


@dataclass(slots=True)
class ReviewPublisherRecorder:
    posts: int = 0

    async def post_for_voting(self) -> None:
        self.posts += 1


@dataclass(frozen=True, slots=True)
class _Services:
    build_queries: BuildQueryRecorder


@dataclass(frozen=True, slots=True)
class _Bot:
    services: _Services
    publisher: ReviewPublisherRecorder

    def for_build(self, build: Build) -> ReviewPublisherRecorder:
        assert build is self.services.build_queries.build
        return self.publisher


def _handler(build: Build) -> tuple[PostSubmittedBuildHandler, BuildQueryRecorder, ReviewPublisherRecorder]:
    queries = BuildQueryRecorder(build)
    publisher = ReviewPublisherRecorder()
    bot = cast(Any, _Bot(_Services(queries), publisher))
    return PostSubmittedBuildHandler(bot), queries, publisher


async def test_submitted_build_is_sent_to_the_review_publisher() -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    handler, queries, publisher = _handler(build)

    await handler.handle(_event(event_type="build.submitted", schema_version=2))

    assert queries.requested == [42]
    assert publisher.posts == 1


async def test_submitted_build_redelivery_reuses_the_same_review_publisher_path() -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    handler, queries, publisher = _handler(build)
    event = _event(event_type="build.submitted", schema_version=2)

    await handler.handle(event)
    await handler.handle(event)

    assert queries.requested == [42, 42]
    assert publisher.posts == 2


async def test_submitted_build_with_unsupported_schema_is_rejected() -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    handler, queries, publisher = _handler(build)

    with pytest.raises(UnsupportedEventVersionError, match="schema version 99"):
        await handler.handle(_event(event_type="build.submitted", schema_version=99))

    assert queries.requested == []
    assert publisher.posts == 0
