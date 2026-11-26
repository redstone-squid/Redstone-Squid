"""Refresh the search index from queued changes to application resources.

A search document is a searchable copy of a build, record, or metadata resource.
Projection work rebuilds that copy from current data, or removes it when the
resource is deleted or no longer eligible. The application function commits document changes
and queue acknowledgements together so a rollback leaves the work available.
"""

import time
from typing import Protocol

from squid.core.actors import ActingContext
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory
from squid.core.worker_context import worker_scope


class SearchProjectionRepository(Protocol):
    """Refresh queued documents in the caller's transaction."""

    async def process_batch(self, *, context: ActingContext, limit: int = 50) -> tuple[int, int]: ...


async def refresh_search_index(
    uow_factory: UnitOfWorkFactory[SearchProjectionUnitOfWork],
    *,
    context: ActingContext,
    limit: int = 50,
    budget_seconds: float = 10,
) -> tuple[int, int]:
    """Refresh queued documents until the queue runs dry or the budget ends; return (succeeded, failed).

    Each batch of up to limit commits on its own. A backlog keeps draining at full speed
    instead of one batch per poll, while the budget bounds how long one run holds the job.
    """
    deadline = time.monotonic() + budget_seconds
    succeeded = failed = 0
    with worker_scope(context):
        while True:
            async with uow_factory() as uow:
                batch_succeeded, batch_failed = await uow.search_projection.process_batch(limit=limit, context=context)
                await uow.commit()
            succeeded += batch_succeeded
            failed += batch_failed
            if batch_succeeded + batch_failed < limit or time.monotonic() >= deadline:
                return succeeded, failed


class SearchProjectionUnitOfWork(UnitOfWork, Protocol):
    """Own the transaction used to refresh search documents."""

    @property
    def search_projection(self) -> SearchProjectionRepository: ...
