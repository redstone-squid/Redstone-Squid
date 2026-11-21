"""Domain-event dispatcher tests."""

from dataclasses import dataclass, field
from typing import override

from whenever import Instant

from squid.bot.events.cog import CONSUMER, DELIVERY_REQUIRED_EVENTS
from squid.core.events import DomainEvent, DomainEventDelivery, DomainEventService, UnsupportedEventVersionError
from squid.core.events.dispatcher import DomainEventRunner


def _delivery(event_type: str = "build.confirmed", *, schema_version: int = 1) -> DomainEventDelivery:
    event = DomainEvent(
        id=1,
        event_type=event_type,
        aggregate_kind="build",
        aggregate_id=42,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={},
        schema_version=schema_version,
    )
    return DomainEventDelivery(event=event, consumer="discord", attempts=0, claimed_at=Instant.from_utc(2026, 8, 9))


@dataclass(slots=True)
class EventServiceRecorder(DomainEventService):
    completed: list[DomainEventDelivery] = field(default_factory=list)
    failed: list[tuple[DomainEventDelivery, Exception]] = field(default_factory=list)
    rejected: list[tuple[DomainEventDelivery, Exception]] = field(default_factory=list)

    @override
    async def complete(self, delivery: DomainEventDelivery) -> bool:
        self.completed.append(delivery)
        return True

    @override
    async def fail(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        self.failed.append((delivery, error))
        return False

    @override
    async def reject(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        self.rejected.append((delivery, error))
        return True


@dataclass(slots=True)
class HandlerRecorder:
    error: Exception | None = None
    events: list[DomainEvent] = field(default_factory=list)

    async def handle(self, event: DomainEvent) -> None:
        self.events.append(event)
        if self.error is not None:
            raise self.error


def _runner(*handlers: HandlerRecorder) -> tuple[DomainEventRunner, EventServiceRecorder]:
    events = EventServiceRecorder()
    registry = {"build.confirmed": tuple(handler.handle for handler in handlers)}
    return DomainEventRunner(events, CONSUMER, registry, required=DELIVERY_REQUIRED_EVENTS), events


async def test_a_handled_event_is_acknowledged() -> None:
    handler = HandlerRecorder()
    runner, events = _runner(handler)
    delivery = _delivery()

    await runner.process(delivery)

    assert handler.events == [delivery.event]
    assert events.completed == [delivery]
    assert events.failed == []


async def test_an_unhandled_event_type_is_acknowledged_rather_than_retried() -> None:
    """Every consumer sees every event, so an unhandled type is normal."""
    runner, events = _runner()
    delivery = _delivery("vote_session.closed")

    await runner.process(delivery)

    assert events.completed == [delivery]
    assert events.failed == []


async def test_missing_submission_delivery_handler_is_not_acknowledged() -> None:
    runner, events = _runner()
    delivery = _delivery("build.submitted")

    await runner.process(delivery)

    assert len(events.failed) == 1
    assert events.failed[0][0] == delivery
    assert events.completed == []


async def test_a_failing_handler_is_retried_and_not_acknowledged() -> None:
    handler = HandlerRecorder(RuntimeError("boom"))
    runner, events = _runner(handler)
    delivery = _delivery()

    await runner.process(delivery)

    assert len(events.failed) == 1
    assert events.completed == []


async def test_an_unsupported_event_version_is_rejected() -> None:
    handler = HandlerRecorder(UnsupportedEventVersionError("unsupported"))
    runner, events = _runner(handler)
    delivery = _delivery(schema_version=99)

    await runner.process(delivery)

    assert len(events.rejected) == 1
    assert events.failed == []
    assert events.completed == []


async def test_every_handler_for_one_event_type_runs() -> None:
    first, second = HandlerRecorder(), HandlerRecorder()
    runner, events = _runner(first, second)
    delivery = _delivery()

    await runner.process(delivery)

    assert first.events == [delivery.event]
    assert second.events == [delivery.event]
    assert events.completed == [delivery]


async def test_a_failing_handler_stops_the_ones_after_it_and_retries_the_delivery() -> None:
    """The whole delivery retries, which is why handlers must be idempotent."""
    failing, later = HandlerRecorder(RuntimeError("boom")), HandlerRecorder()
    runner, events = _runner(failing, later)

    await runner.process(_delivery())

    assert later.events == []
    assert len(events.failed) == 1
    assert events.completed == []
