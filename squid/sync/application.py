"""Application service for durable Discord reconciliation work."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from whenever import Instant

from squid.posts.domain import ResourceKind as PostResourceKind


class ReconciliationResource(StrEnum):
    """What kind of Discord-rendered resource a row asks to repair."""

    BUILD = "build"
    VOTE_SESSION = "vote_session"
    STARBOARD_ENTRY = "starboard_entry"

    @property
    def post_kind(self) -> PostResourceKind:
        """The same value, spelled as the posts context types it.

        Written out rather than cast so adding a resource fails here instead of
        at the renderer lookup. The two contexts naming one set of resources
        twice is real debt; closing it means converting
        `squid.posts.domain.ResourceKind` as well, which reaches the starboard
        paths this review excludes.
        """
        match self:
            case ReconciliationResource.BUILD:
                return "build"
            case ReconciliationResource.VOTE_SESSION:
                return "vote_session"
            case ReconciliationResource.STARBOARD_ENTRY:
                return "starboard_entry"


class ReconciliationAction(StrEnum):
    """What a row asks for once its resource is loaded."""

    REFRESH = "refresh"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ReconciliationJob:
    """One claimed request to bring a Discord resource back in line.

    This is a desired-state queue, not an event log, and the difference decides
    how every field behaves. Rows are coalesced by `(resource_kind, source_key)`
    in the database triggers that write them, deleted on acknowledgement, and
    `generation` is a staleness token drawn from a sequence and compared against
    the post's applied revision rather than an ordering. An event log would be
    append-only and replayable; this is neither, and re-reading a row tells you
    what the resource should look like *now*, not what happened to it.
    """

    id: int
    resource_kind: ReconciliationResource
    source_key: str
    action: ReconciliationAction
    generation: int
    attempts: int
    claimed_at: Instant


class ReconciliationQueue(Protocol):
    """Persistence required by the Discord reconciliation drainer."""

    async def claim(self, *, limit: int) -> Sequence[ReconciliationJob]: ...

    async def complete(self, job: ReconciliationJob) -> bool: ...

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool: ...


class DiscordReconciliationService:
    """Claim and acknowledge durable Discord refresh requests."""

    def __init__(self, repository: ReconciliationQueue, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, limit: int = 20) -> Sequence[ReconciliationJob]:
        """Claim ready work, reclaiming jobs abandoned by crashed workers."""
        if not 1 <= limit <= 100:
            msg = "claim limit must be between 1 and 100"
            raise ValueError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: ReconciliationJob) -> bool:
        """Acknowledge a job only if its claim is still current."""
        return await self._repository.complete(job)

    async def fail(self, job: ReconciliationJob, error: Exception) -> bool:
        """Retry failed work with backoff, or dead-letter it at the attempt ceiling.

        Returns whether the job was dead-lettered.
        """
        return await self._repository.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
