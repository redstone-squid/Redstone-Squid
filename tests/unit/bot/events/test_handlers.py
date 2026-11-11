"""Domain events delegate submission review work to the publisher.

Confirming a build queues a Discord refresh; the post reconciler publishes its card.
That separate path is covered in ``tests/unit/bot/posts/test_reconciler.py``.
"""

from dataclasses import dataclass
from typing import Any, cast, override

import pytest
from whenever import Instant

from squid.bot.events.handlers import PostSubmittedBuildHandler
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.models import Build, OtherBuild
from squid.events import DomainEvent, UnsupportedEventVersionError
from squid.runtime import BotServices
from tests.support.services import bot_services


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


@pytest.fixture
def review_calls(monkeypatch: pytest.MonkeyPatch) -> list[Build]:
    calls: list[Build] = []

    async def channels(bot: object) -> tuple[()]:
        return ()

    async def review(bot: object, build: Build, channels: object) -> None:
        calls.append(build)

    monkeypatch.setattr("squid.bot.events.handlers.configured_vote_channels", channels)
    monkeypatch.setattr("squid.bot.events.handlers.ensure_build_review", review)
    return calls


@dataclass(frozen=True, slots=True)
class _Bot:
    services: BotServices


def _handler(build: Build) -> tuple[PostSubmittedBuildHandler, BuildQueryRecorder]:
    queries = BuildQueryRecorder(build)
    bot = cast(Any, _Bot(bot_services(build_queries=queries)))
    return PostSubmittedBuildHandler(bot), queries


async def test_submitted_build_is_sent_to_the_review_publisher(review_calls: list[Build]) -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    handler, queries = _handler(build)

    await handler.handle(_event(event_type="build.submitted", schema_version=2))

    assert queries.requested == [42]
    assert review_calls == [build]


async def test_submitted_build_redelivery_reuses_the_same_review_publisher_path(review_calls: list[Build]) -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    build.submission_status = Status.PENDING
    handler, queries = _handler(build)
    event = _event(event_type="build.submitted", schema_version=2)

    await handler.handle(event)
    await handler.handle(event)

    assert queries.requested == [42, 42]
    assert review_calls == [build, build]


async def test_submitted_build_with_unsupported_schema_is_rejected(review_calls: list[Build]) -> None:
    build = OtherBuild(id=42, submitter_account_id=7)
    handler, queries = _handler(build)

    with pytest.raises(UnsupportedEventVersionError, match="schema version 99"):
        await handler.handle(_event(event_type="build.submitted", schema_version=99))

    assert queries.requested == []
    assert review_calls == []
