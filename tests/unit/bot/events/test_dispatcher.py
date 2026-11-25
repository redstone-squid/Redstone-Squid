"""Domain-event dispatcher tests."""

from dataclasses import dataclass, field
from typing import override

from whenever import Instant

from squid.bot.app import RedstoneSquid
from squid.bot.events.cog import CONSUMER, DELIVERY_REQUIRED_EVENTS, DomainEventCog
from squid.core.actors import ActingContext
from squid.core.events import DomainEvent, DomainEventDelivery, DomainEventService, UnsupportedEventVersionError
from squid.core.events.dispatcher import DomainEventRunner
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


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
    pending: list[DomainEventDelivery] = field(default_factory=list)
    completed: list[DomainEventDelivery] = field(default_factory=list)
    failed: list[tuple[DomainEventDelivery, Exception]] = field(default_factory=list)
    rejected: list[tuple[DomainEventDelivery, Exception]] = field(default_factory=list)

    @override
    async def claim(self, consumer: str, limit: int = 20, *, context: ActingContext) -> tuple[DomainEventDelivery, ...]:
        assert consumer == CONSUMER
        deliveries = tuple(self.pending[:limit])
        del self.pending[:limit]
        return deliveries

    @override
    async def complete(self, delivery: DomainEventDelivery, *, context: ActingContext) -> bool:
        self.completed.append(delivery)
        return True

    @override
    async def fail(self, delivery: DomainEventDelivery, error: Exception, *, context: ActingContext) -> bool:
        self.failed.append((delivery, error))
        return False

    @override
    async def reject(self, delivery: DomainEventDelivery, error: Exception, *, context: ActingContext) -> bool:
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

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert handler.events == [delivery.event]
    assert events.completed == [delivery]
    assert events.failed == []


async def test_an_unhandled_event_type_is_acknowledged_rather_than_retried() -> None:
    """Every consumer sees every event, so an unhandled type is normal."""
    runner, events = _runner()
    delivery = _delivery("vote_session.closed")

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert events.completed == [delivery]
    assert events.failed == []


async def test_missing_submission_delivery_handler_is_not_acknowledged() -> None:
    runner, events = _runner()
    delivery = _delivery("build.submitted")

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert len(events.failed) == 1
    assert events.failed[0][0] == delivery
    assert events.completed == []


async def test_a_failing_handler_is_retried_and_not_acknowledged() -> None:
    handler = HandlerRecorder(RuntimeError("boom"))
    runner, events = _runner(handler)
    delivery = _delivery()

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert len(events.failed) == 1
    assert events.completed == []


async def test_an_unsupported_event_version_is_rejected() -> None:
    handler = HandlerRecorder(UnsupportedEventVersionError("unsupported"))
    runner, events = _runner(handler)
    delivery = _delivery(schema_version=99)

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert len(events.rejected) == 1
    assert events.failed == []
    assert events.completed == []


async def test_every_handler_for_one_event_type_runs() -> None:
    first, second = HandlerRecorder(), HandlerRecorder()
    runner, events = _runner(first, second)
    delivery = _delivery()

    await runner.process(delivery, context=ActingContext.worker("test-worker"))

    assert first.events == [delivery.event]
    assert second.events == [delivery.event]
    assert events.completed == [delivery]


async def test_a_failing_handler_stops_the_ones_after_it_and_retries_the_delivery() -> None:
    """The whole delivery retries, which is why handlers must be idempotent."""
    failing, later = HandlerRecorder(RuntimeError("boom")), HandlerRecorder()
    runner, events = _runner(failing, later)

    await runner.process(_delivery(), context=ActingContext.worker("test-worker"))

    assert later.events == []
    assert len(events.failed) == 1
    assert events.completed == []


async def test_detaching_a_subscription_leaves_other_registrations_active() -> None:
    async def ready() -> None:
        pass

    events = EventServiceRecorder()
    bot: RedstoneSquid = make_layout_bot(services=bot_services(domain_events=events), wait_until_ready=ready)
    cog = DomainEventCog(bot)
    handler = HandlerRecorder()
    delivery = _delivery("vote_session.closed")
    handlers = {delivery.event.event_type: handler.handle}
    detach_first = cog.subscribe(handlers)
    detach_second = cog.subscribe(handlers)

    events.pending.append(delivery)
    await cog.process_domain_events()
    assert handler.events == [delivery.event, delivery.event]

    await detach_first()
    events.pending.append(delivery)
    await cog.process_domain_events()
    assert handler.events == [delivery.event] * 3

    await detach_second()
    events.pending.append(delivery)
    await cog.process_domain_events()
    assert handler.events == [delivery.event] * 3
    assert events.completed == [delivery] * 3
