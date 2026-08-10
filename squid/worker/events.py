"""Transport-neutral domain-event handlers owned by the worker process."""

import logging
from typing import Protocol

from squid.events import DomainEvent, DomainEventDelivery, DomainEventService, UnsupportedEventVersionError
from squid.notifications import NotificationService
from squid.voting.domain import VoteSessionSnapshot

logger = logging.getLogger(__name__)
CORE_CONSUMER = "core"


class VoteOutcomeReader(Protocol):
    """Read the authoritative state of a closed vote session."""

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None: ...


class BuildStatusWriter(Protocol):
    """Apply the final moderation status of a build."""

    async def confirm(self, build_id: int) -> object: ...

    async def deny(self, build_id: int) -> object: ...


class CoreEventHandler(Protocol):
    """Handle one transport-neutral domain event idempotently."""

    async def handle(self, event: DomainEvent) -> None: ...


class ApplyBuildVoteOutcomeHandler:
    """Apply a closed build vote's outcome without depending on Discord."""

    def __init__(self, votes: VoteOutcomeReader, builds: BuildStatusWriter) -> None:
        self._votes = votes
        self._builds = builds

    async def handle(self, event: DomainEvent) -> None:
        snapshot = await self._votes.get_session_by_id(event.aggregate_id)
        if snapshot is None or snapshot.kind != "build" or snapshot.status != "closed":
            return
        build_id = snapshot.target.build_id
        if build_id is None:
            return
        if snapshot.result == "approved":
            await self._builds.confirm(build_id)
        elif snapshot.result == "denied":
            await self._builds.deny(build_id)


class MaterializeNotificationHandler:
    """Project supported events into durable inbox and DM work."""

    def __init__(self, notifications: NotificationService) -> None:
        self._notifications = notifications

    async def handle(self, event: DomainEvent) -> None:
        supported_versions = {
            "build.submitted": {1},
            "build.confirmed": {1, 2},
            "build.denied": {1, 2},
            "record_run.activated": {1},
        }
        if event.schema_version not in supported_versions.get(event.event_type, set()):
            msg = f"Unsupported {event.event_type} schema version {event.schema_version}"
            raise UnsupportedEventVersionError(msg)
        await self._notifications.materialize(event)


class CoreDomainEventRunner:
    """Drain and acknowledge the core consumer's durable deliveries."""

    def __init__(self, events: DomainEventService, handlers: dict[str, tuple[CoreEventHandler, ...]]) -> None:
        self._events = events
        self._handlers = handlers

    async def process_batch(self) -> None:
        """Process one bounded batch, isolating failure to each delivery."""
        for delivery in await self._events.claim(CORE_CONSUMER):
            await self._process(delivery)

    async def _process(self, delivery: DomainEventDelivery) -> None:
        try:
            for handler in self._handlers.get(delivery.event.event_type, ()):
                await handler.handle(delivery.event)
        except UnsupportedEventVersionError as error:
            await self._events.reject(delivery, error)
            logger.exception(
                "Rejected a domain event with an unsupported schema version",
                extra={
                    "squid.event.id": delivery.event.id,
                    "squid.event.type": delivery.event.event_type,
                    "squid.event.schema_version": delivery.event.schema_version,
                },
            )
            return
        except Exception as error:
            dead_lettered = await self._events.fail(delivery, error)
            if dead_lettered:
                logger.exception(
                    "Dead-lettered a core domain event after repeated handler failures",
                    extra={
                        "squid.event.id": delivery.event.id,
                        "squid.event.type": delivery.event.event_type,
                    },
                )
            return
        await self._events.complete(delivery)
