"""Worker event tests: the worker serves no request and knows no chat client."""

from typing import Any, cast

from whenever import Instant

from squid.builds.domain import Build
from squid.config import WorkerConfig
from squid.events import DomainEvent, DomainEventDelivery, UnsupportedEventVersionError
from squid.voting.domain import BuildVoteTarget, VoteSessionResult, VoteSessionSnapshot, VoteStatus
from squid.worker.app import DatabaseWorker
from squid.worker.events import ApplyBuildVoteOutcomeHandler, CoreDomainEventRunner, MaterializeNotificationHandler
from tests.support.voting import build_snapshot
from tests.unit.worker.fakes import (
    StubBuildService,
    StubEventService,
    StubNotificationService,
    StubVoteService,
    SupervisorRecorder,
    worker_services,
)


class VoteRecorder(StubVoteService):
    def __init__(self, snapshot: VoteSessionSnapshot) -> None:
        self.snapshot = snapshot
        self.requested_ids: list[int] = []

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot:
        self.requested_ids.append(vote_session_id)
        return self.snapshot


class BuildRecorder(StubBuildService):
    def __init__(self) -> None:
        self.confirmed: list[int] = []
        self.denied: list[int] = []

    async def confirm(self, build_id: int) -> Build:
        self.confirmed.append(build_id)
        return cast(Build, object())

    async def deny(self, build_id: int) -> Build:
        self.denied.append(build_id)
        return cast(Build, object())


class EventRecorder(StubEventService):
    def __init__(self, *deliveries: DomainEventDelivery) -> None:
        self.deliveries = deliveries
        self.completed: list[DomainEventDelivery] = []
        self.failed: list[tuple[DomainEventDelivery, Exception]] = []
        self.rejected: list[tuple[DomainEventDelivery, Exception]] = []

    async def claim(self, consumer: str, limit: int = 20) -> tuple[DomainEventDelivery, ...]:
        return self.deliveries

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        self.completed.append(delivery)
        return True

    async def fail(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        self.failed.append((delivery, error))
        return False

    async def reject(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        self.rejected.append((delivery, error))
        return True


class FailingHandler:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def handle(self, event: DomainEvent) -> None:
        raise self.error


class NotificationRecorder(StubNotificationService):
    def __init__(self) -> None:
        self.materialized: list[DomainEvent] = []

    async def materialize(self, event: DomainEvent) -> None:
        self.materialized.append(event)


def _event() -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=7,
        occurred_at=Instant.now(),
    )


def _snapshot(result: VoteSessionResult) -> VoteSessionSnapshot:
    return build_snapshot(
        id=7,
        author_account_id=1,
        status=VoteStatus.CLOSED,
        result=result,
        pass_threshold=1,
        fail_threshold=-1,
        messages=(),
        target=BuildVoteTarget(42),
    )


async def test_apply_build_vote_outcome_names_no_chat_client() -> None:
    votes = VoteRecorder(_snapshot(VoteSessionResult.APPROVED))
    builds = BuildRecorder()
    handler = ApplyBuildVoteOutcomeHandler(votes, builds)

    await handler.handle(_event())

    assert builds.confirmed == [42]
    assert builds.denied == []


async def test_core_runner_acknowledges_unhandled_events() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = EventRecorder(delivery)
    runner = CoreDomainEventRunner(events, {})

    await runner.process_batch()

    assert events.completed == [delivery]
    assert events.failed == []


async def test_core_runner_retries_handler_failure() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = EventRecorder(delivery)
    handler = FailingHandler(RuntimeError("boom"))
    runner = CoreDomainEventRunner(events, {"vote_session.closed": (handler,)})

    await runner.process_batch()

    assert len(events.failed) == 1
    assert events.failed[0][0] == delivery
    assert isinstance(events.failed[0][1], RuntimeError)
    assert events.completed == []


async def test_core_runner_rejects_unsupported_event_versions_without_retry() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = EventRecorder(delivery)
    error = UnsupportedEventVersionError("future")
    handler = FailingHandler(error)
    runner = CoreDomainEventRunner(events, {"vote_session.closed": (handler,)})

    await runner.process_batch()

    assert events.rejected == [(delivery, error)]
    assert events.failed == []
    assert events.completed == []


async def test_notification_handler_accepts_current_account_keyed_build_event_versions() -> None:
    notifications = NotificationRecorder()
    handler = MaterializeNotificationHandler(notifications)

    events = tuple(
        DomainEvent(
            id=index,
            event_type=event_type,
            aggregate_kind="build",
            aggregate_id=42,
            occurred_at=Instant.now(),
            schema_version=schema_version,
        )
        for index, (event_type, schema_version) in enumerate(
            (("build.submitted", 2), ("build.confirmed", 3), ("build.denied", 3)),
            start=1,
        )
    )

    for event in events:
        await handler.handle(event)

    assert notifications.materialized == list(events)


async def test_periodic_poll_materializes_an_event_without_a_listen_wake_hint() -> None:
    event = DomainEvent(
        id=1,
        event_type="build.confirmed",
        aggregate_kind="build",
        aggregate_id=42,
        occurred_at=Instant.now(),
        schema_version=3,
    )
    delivery = DomainEventDelivery(event=event, consumer="core", attempts=0, claimed_at=Instant.now())
    events = EventRecorder(delivery)
    notifications = NotificationRecorder()
    supervisor = SupervisorRecorder()
    worker = DatabaseWorker(
        worker_services(events=events, notifications=notifications),
        cast(Any, object()),
        WorkerConfig(event_interval_seconds=7),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
    )

    worker.start()
    periodic_poll = supervisor.job("core-domain-events")
    await periodic_poll.operation()

    assert periodic_poll.interval == 7
    assert notifications.materialized == [event]
    assert events.completed == [delivery]
