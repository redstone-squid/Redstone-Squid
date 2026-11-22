"""Worker reactions for the core domain-event consumer."""

from typing import Protocol

from squid.core.builds.history import BuildEditActor, HistoryOperation
from squid.core.events import DomainEvent, UnsupportedEventVersionError
from squid.core.notifications import NotificationService
from squid.core.voting.application.services import VoteService
from squid.core.voting.domain import BuildVoteTarget, VoteSessionResult, VoteSessionSnapshot

_NOTIFICATION_EVENT_SCHEMA_VERSIONS = {
    "build.submitted": frozenset({1, 2}),
    "build.confirmed": frozenset({1, 2, 3}),
    "build.denied": frozenset({1, 2, 3}),
    "record_run.activated": frozenset({1}),
}


class VoteOutcomeReader(Protocol):
    """Read the authoritative state of a closed vote session."""

    async def get_session(self, vote_session_id: int) -> VoteSessionSnapshot | None: ...


class BuildStatusWriter(Protocol):
    """Apply the final moderation status of a build."""

    async def approve(
        self,
        build_id: int,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "moderation",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> object: ...

    async def reject(
        self,
        build_id: int,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "moderation",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> object: ...


class ApplyBuildVoteOutcomeHandler:
    """Apply a closed build vote's outcome without depending on Discord."""

    def __init__(self, votes: VoteOutcomeReader, builds: BuildStatusWriter) -> None:
        self._votes = votes
        self._builds = builds

    async def handle(self, event: DomainEvent) -> None:
        snapshot = await self._votes.get_session(event.aggregate_id)
        if snapshot is None or snapshot.is_open or not isinstance(snapshot.target, BuildVoteTarget):
            return
        if snapshot.result is VoteSessionResult.APPROVED:
            await self._builds.approve(
                snapshot.target.build_id,
                actor="vote_worker",
                operation="moderation",
                source="vote_outcome",
                correlation_kind="domain_event",
                correlation_id=str(event.id),
            )
        elif snapshot.result is VoteSessionResult.DENIED:
            await self._builds.reject(
                snapshot.target.build_id,
                actor="vote_worker",
                operation="moderation",
                source="vote_outcome",
                correlation_kind="domain_event",
                correlation_id=str(event.id),
            )


class RefreshGuildVotingHandler:
    """Refresh from current settings, so delayed or repeated events are safe."""

    def __init__(self, votes: VoteService) -> None:
        self._votes = votes

    async def handle(self, event: DomainEvent) -> None:
        if event.schema_version != 1:
            raise UnsupportedEventVersionError
        await self._votes.refresh_configuration(event.aggregate_id)


class MaterializeNotificationHandler:
    """Project supported events into durable inbox and DM work."""

    def __init__(self, notifications: NotificationService) -> None:
        self._notifications = notifications

    async def handle(self, event: DomainEvent) -> None:
        if event.schema_version not in _NOTIFICATION_EVENT_SCHEMA_VERSIONS.get(event.event_type, frozenset()):
            msg = f"Unsupported {event.event_type} schema version {event.schema_version}"
            raise UnsupportedEventVersionError(msg)
        await self._notifications.materialize(event)
