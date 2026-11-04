"""Keep bot-owned Discord posts in step with their application resources.

Reconciliation means reading a resource's current state and updating its posts
to match. Database triggers queue refresh or deletion requests, and the bot
claims those requests through DiscordReconciliationService before calling the
appropriate renderer. Repeated changes to one resource share a pending request;
this queue tracks the latest required display, not a history of every change.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.posts.domain import ResourceKind as PostResourceKind
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class ReconciliationResource(StrEnum):
    """What kind of Discord-rendered resource a row asks to repair."""

    BUILD = "build"
    VOTE_SESSION = "vote_session"
    STARBOARD_ENTRY = "starboard_entry"
    INFERENCE_RUN = "inference_run"
    SUBMISSION_DRAFT = "submission_draft"

    @property
    def post_kind(self) -> PostResourceKind:
        """Return the literal resource name expected by post renderers.

        Explicit cases let type checkers detect a newly added enum member that
        has not been mapped here.
        """
        match self:
            case ReconciliationResource.BUILD:
                return "build"
            case ReconciliationResource.VOTE_SESSION:
                return "vote_session"
            case ReconciliationResource.STARBOARD_ENTRY:
                return "starboard_entry"
            case ReconciliationResource.INFERENCE_RUN:
                return "inference_run"
            case ReconciliationResource.SUBMISSION_DRAFT:
                return "submission_draft"


class ReconciliationAction(StrEnum):
    """What a row asks for once its resource is loaded."""

    REFRESH = "refresh"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ReconciliationJob:
    """One claimed request to update a resource's Discord posts.

    Triggers keep one queue row per resource, replacing its requested action when
    the resource changes again. Workers render the current state, so intermediate
    changes need not produce separate Discord updates. Completion deletes the row.

    The generation identifies the requested update. Compare it with each post's
    applied revision to find posts that still need rendering.
    """

    id: int
    resource_kind: ReconciliationResource
    source_key: str
    action: ReconciliationAction
    generation: int
    attempts: int
    claim_token: uuid.UUID
    """Token identifying this claim; acknowledgements must still match it."""


class ReconciliationQueue(Protocol):
    """Persistence required by the Discord reconciliation drainer."""

    async def claim(self, *, limit: int) -> Sequence[ReconciliationJob]: ...

    async def complete(self, job: ReconciliationJob) -> bool: ...

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool: ...


class DiscordReconciliationService:
    """Claim and acknowledge durable Discord refresh requests."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[ReconciliationUnitOfWork], max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = tr(t"max_attempts must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts

    async def claim(self, limit: int = 20) -> Sequence[ReconciliationJob]:
        """Claim ready work and commit its leases, including replacements for expired claims."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= 100:
                msg = tr(t"claim limit must be between 1 and 100")
                raise InvalidStateError(msg)
            result = await uow.reconciliation.claim(limit=limit)
            await uow.commit()
            return result

    async def complete(self, job: ReconciliationJob) -> bool:
        """Remove a job and return True only if its claim still matches."""
        async with self._uow_factory() as uow:
            result = await uow.reconciliation.complete(job)
            await uow.commit()
            return result

    async def fail(self, job: ReconciliationJob, error: Exception) -> bool:
        """Retry failed work with backoff, or dead-letter it at the attempt ceiling.

        Return True if retries were stopped. False means a retry was scheduled or the claim was lost.
        """
        async with self._uow_factory() as uow:
            result = await uow.reconciliation.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
            await uow.commit()
            return result


class ReconciliationUnitOfWork(UnitOfWork, Protocol):
    @property
    def reconciliation(self) -> ReconciliationQueue: ...
