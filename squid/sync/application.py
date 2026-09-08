"""Application service for durable Discord reconciliation work."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.posts.domain import ResourceKind as PostResourceKind


class ReconciliationResource(StrEnum):
    """What kind of Discord-rendered resource a row asks to repair."""

    BUILD = "build"
    VOTE_SESSION = "vote_session"
    STARBOARD_ENTRY = "starboard_entry"

    @property
    def post_kind(self) -> PostResourceKind:
        """The same value, spelled as the posts context types it.

        Written out rather than cast, so a new resource fails to type-check here instead of at the renderer lookup.
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

    Desired state, not an event: rows are coalesced by `(resource_kind, source_key)` and deleted on
    acknowledgement, so a job says what the resource should look like now rather than what happened to it.
    `generation` is a staleness token compared against a post's applied revision, not an ordering.
    """

    id: int
    resource_kind: ReconciliationResource
    source_key: str
    action: ReconciliationAction
    generation: int
    attempts: int
    claim_token: uuid.UUID
    """The database-minted fence this worker's acknowledgement must still match."""


class ReconciliationQueue(Protocol):
    """Persistence required by the Discord reconciliation drainer.

    Acknowledgement is fenced on the job's claim token: a worker whose claim expired and was taken over applies
    nothing and gets `False`.

    Raises `DataIntegrityError` from `claim` for a row whose resource kind or action the check constraints should
    have rejected.
    """

    async def claim(self, *, limit: int) -> Sequence[ReconciliationJob]:
        """Claim up to `limit` ready jobs, including ones whose earlier claim expired."""
        ...

    async def complete(self, job: ReconciliationJob) -> bool:
        """Delete the job's row, returning whether the claim was still current."""
        ...

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        """Release the job for a later attempt with backoff, recording `error`.

        Returns whether this failure dead-lettered it by reaching `max_attempts`.
        """
        ...


class DiscordReconciliationService:
    """Claim and acknowledge durable Discord refresh requests.

    Construction raises `InvalidStateError` unless `max_attempts` is positive.
    """

    def __init__(self, repository: ReconciliationQueue, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = tr(t"max_attempts must be positive")
            raise InvalidStateError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, limit: int = 20) -> Sequence[ReconciliationJob]:
        """Claim ready work, reclaiming jobs abandoned by crashed workers.

        Raises `InvalidStateError` if `limit` is outside 1-100.
        """
        if not 1 <= limit <= 100:
            msg = tr(t"claim limit must be between 1 and 100")
            raise InvalidStateError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: ReconciliationJob) -> bool:
        """Acknowledge a job only if its claim is still current."""
        return await self._repository.complete(job)

    async def fail(self, job: ReconciliationJob, error: Exception) -> bool:
        """Retry failed work with backoff, or dead-letter it at the attempt ceiling.

        Returns whether the job was dead-lettered.
        """
        return await self._repository.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
