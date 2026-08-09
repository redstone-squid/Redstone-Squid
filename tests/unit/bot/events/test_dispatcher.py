"""Domain-event dispatcher tests."""

from typing import Any
from unittest.mock import AsyncMock

from whenever import Instant

from squid.bot.events.dispatcher import DomainEventCog
from squid.events import DomainEvent, DomainEventDelivery


def _delivery(event_type: str = "build.confirmed") -> DomainEventDelivery:
    event = DomainEvent(
        id=1,
        event_type=event_type,
        aggregate_kind="build",
        aggregate_id=42,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={},
    )
    return DomainEventDelivery(event=event, consumer="discord", attempts=0, claimed_at=Instant.from_utc(2026, 8, 9))


def _cog(handler: Any) -> tuple[Any, Any]:
    """Build a cog without starting its loop, so dispatch can be driven directly."""
    cog = DomainEventCog.__new__(DomainEventCog)
    bot = AsyncMock()
    cog.bot = bot
    cog.handlers = {"build.confirmed": handler} if handler is not None else {}
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
    cog, bot = _cog(None)

    await cog._process(_delivery("vote_session.closed"))

    bot.services.domain_events.complete.assert_awaited_once()
    bot.services.domain_events.fail.assert_not_awaited()


async def test_a_failing_handler_is_retried_and_not_acknowledged() -> None:
    handler = AsyncMock()
    handler.handle.side_effect = RuntimeError("boom")
    cog, bot = _cog(handler)
    bot.services.domain_events.fail.return_value = False

    await cog._process(_delivery())

    bot.services.domain_events.fail.assert_awaited_once()
    bot.services.domain_events.complete.assert_not_awaited()
