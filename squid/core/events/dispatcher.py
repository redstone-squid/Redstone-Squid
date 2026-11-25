"""Deliver recorded facts to the subscribers in one named consumer."""

import logging
from collections.abc import Awaitable, Callable, Mapping

from squid.core.actors import ActingContext
from squid.core.diagnostics.log_capture import work_lost
from squid.core.events.application import DomainEventDelivery, DomainEventService, UnsupportedEventVersionError
from squid.core.events.models import DomainEvent
from squid.core.worker_context import worker_execution, worker_scope

logger = logging.getLogger(__name__)
type EventHandler = Callable[[DomainEvent], Awaitable[None]]


class DomainEventRunner:
    """Run handlers sequentially and acknowledge each delivery after all succeed.

    Events with no registered handler are acknowledged. Unsupported schema versions
    are rejected; other handler failures use the queue's retry policy. A retry runs
    all handlers again, so each must tolerate previously completed side effects.
    """

    def __init__(
        self,
        events: DomainEventService,
        consumer: str,
        handlers: Mapping[str, tuple[EventHandler, ...]],
        *,
        required: frozenset[str] = frozenset(),
    ) -> None:
        self.consumer = consumer
        self._required = required
        self._events = events
        self._handlers = handlers

    async def process_batch(self, *, context: ActingContext) -> None:
        """Process a bounded batch; queue-operation errors propagate and stop the batch."""
        with worker_scope(context):
            for delivery in await self._events.claim(self.consumer, context=context):
                await self.process(delivery, context=context)

    async def process(self, delivery: DomainEventDelivery, *, context: ActingContext) -> None:
        with (
            worker_scope(context),
            worker_execution(f"events.{self.consumer}.{delivery.event.event_type}", delivery.claim_token) as attempt,
        ):
            handlers = self._handlers.get(delivery.event.event_type, ())
            if not handlers and delivery.event.event_type in self._required:
                error = RuntimeError(f"Missing {self.consumer} handler for {delivery.event.event_type}")
                await self._events.fail(delivery, error, context=attempt)
                return
            try:
                for handler in handlers:
                    await handler(delivery.event)
            except UnsupportedEventVersionError as error:
                await self._events.reject(delivery, error, context=attempt)
                logger.exception(
                    "Rejected a domain event with an unsupported schema version",
                    extra={
                        "squid.event.consumer": self.consumer,
                        "squid.event.id": delivery.event.id,
                        "squid.event.type": delivery.event.event_type,
                        "squid.event.schema_version": delivery.event.schema_version,
                        **work_lost(),
                    },
                )
                return
            except Exception as error:
                dead_lettered = await self._events.fail(delivery, error, context=attempt)
                if dead_lettered:
                    logger.exception(
                        "Dead-lettered a domain event after repeated handler failures",
                        extra={
                            "squid.event.consumer": self.consumer,
                            "squid.event.id": delivery.event.id,
                            "squid.event.type": delivery.event.event_type,
                            **work_lost(),
                        },
                    )
                return
            await self._events.complete(delivery, context=attempt)
