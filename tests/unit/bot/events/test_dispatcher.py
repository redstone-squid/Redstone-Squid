"""Domain-event dispatcher tests."""

from typing import Any
from unittest.mock import AsyncMock

from whenever import Instant

from squid.bot.events.dispatcher import DomainEventCog
from squid.events import DomainEvent, DomainEventDelivery, UnsupportedEventVersionError


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


def _cog(*handlers: Any) -> tuple[Any, Any]:
    """Build a cog without starting its loop, so dispatch can be driven directly."""
    cog = DomainEventCog.__new__(DomainEventCog)
    bot = AsyncMock()
    cog.bot = bot
    cog.handlers = {"build.confirmed": handlers} if handlers else {}
    return cog, bot


async def test_a_handled_event_is_acknowledged() -> None:
    handler = AsyncMock()
    cog, bot = _cog(handler)

    await cog._process(_delivery())

    handler.handle.assert_awaited_once()
    bot.services.domain_events.complete.assert_awaited_once()
    bot.services.domain_events.fail.assert_not_awaited()


async def test_an_unhandled_event_type_is_acknowledged_rather_than_retried() -> None:
    """Every consumer sees every event, so an unhandled type is normal."""
    cog, bot = _cog()

    await cog._process(_delivery("vote_session.closed"))

    bot.services.domain_events.complete.assert_awaited_once()
    bot.services.domain_events.fail.assert_not_awaited()


async def test_missing_submission_delivery_handler_is_not_acknowledged() -> None:
    cog, bot = _cog()
    bot.services.domain_events.fail.return_value = False

    await cog._process(_delivery("build.submitted"))

    bot.services.domain_events.fail.assert_awaited_once()
    bot.services.domain_events.complete.assert_not_awaited()


async def test_a_failing_handler_is_retried_and_not_acknowledged() -> None:
    handler = AsyncMock()
    handler.handle.side_effect = RuntimeError("boom")
    cog, bot = _cog(handler)
    bot.services.domain_events.fail.return_value = False

    await cog._process(_delivery())

    bot.services.domain_events.fail.assert_awaited_once()
    bot.services.domain_events.complete.assert_not_awaited()


async def test_an_unsupported_event_version_is_rejected() -> None:
    handler = AsyncMock()
    handler.handle.side_effect = UnsupportedEventVersionError("unsupported")
    cog, bot = _cog(handler)

    await cog._process(_delivery(schema_version=99))

    bot.services.domain_events.reject.assert_awaited_once()
    bot.services.domain_events.fail.assert_not_awaited()
    bot.services.domain_events.complete.assert_not_awaited()


async def test_every_handler_for_one_event_type_runs() -> None:
    first, second = AsyncMock(), AsyncMock()
    cog, bot = _cog(first, second)

    await cog._process(_delivery())

    first.handle.assert_awaited_once()
    second.handle.assert_awaited_once()
    bot.services.domain_events.complete.assert_awaited_once()


async def test_a_failing_handler_stops_the_ones_after_it_and_retries_the_delivery() -> None:
    """The whole delivery retries, which is why handlers must be idempotent."""
    failing, later = AsyncMock(), AsyncMock()
    failing.handle.side_effect = RuntimeError("boom")
    cog, bot = _cog(failing, later)
    bot.services.domain_events.fail.return_value = False

    await cog._process(_delivery())

    later.handle.assert_not_awaited()
    bot.services.domain_events.fail.assert_awaited_once()
    bot.services.domain_events.complete.assert_not_awaited()
