"""Claim bookkeeping shared by the trigger-filled outbox tables.

Two outbox tables — `discord_sync_queue` and `domain_event_deliveries` — are drained
the same way: claim a bounded batch with `FOR UPDATE SKIP LOCKED`, stamp a claim
timestamp that doubles as a fencing token, then either delete the row or release it
with exponential backoff. The subtle parts are the reclaim predicate, the fencing
comparison, and the backoff ceiling, so they are defined once here.

Two other queues deliberately do not use this. `SearchProjectionStore` runs inside a
caller-owned session and hands back live ORM rows that the projector mutates in the
same unit of work. `record_recompute_queue` leases whole scopes rather than rows and
acknowledges by scope, so it has no per-row claim token to fence with. Forcing either
through this would change its semantics rather than share its plumbing.
"""

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, delete, func, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute
from whenever import Instant

VISIBILITY_TIMEOUT = timedelta(minutes=5)
"""How long a claim survives before another worker may reclaim it.

This is what recovers work from a process that died mid-job, so it has to exceed the
slowest realistic handler while staying short enough that a crash is not visible for
long.
"""

BASE_RETRY_DELAY = timedelta(seconds=15)
MAX_RETRY_DELAY = timedelta(hours=1)


def reclaimable(claimed_at: InstrumentedAttribute[Instant | None]) -> ColumnElement[bool]:
    """Match rows that are unclaimed, or whose claim has expired."""
    return or_(claimed_at.is_(None), claimed_at < func.now() - VISIBILITY_TIMEOUT)


def retry_delay(attempts: int) -> timedelta:
    """Back off exponentially, capped so even a stuck job still retries hourly."""
    delay = BASE_RETRY_DELAY * 2 ** (attempts - 1)
    return min(delay, MAX_RETRY_DELAY)


class ClaimedRowQueue:
    """Acknowledge or release rows claimed from an outbox table.

    Every write is fenced on the claim timestamp the worker was handed, so a row that
    was reclaimed or re-enqueued underneath a slow worker is left alone rather than
    deleted out from under its new owner.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model: type[Any],
        *,
        ready_at: InstrumentedAttribute[Instant],
        claimed_at: InstrumentedAttribute[Instant | None],
    ) -> None:
        self._session_factory = session_factory
        self._model = model
        self._ready_at = ready_at
        self._claimed_at = claimed_at

    def reclaimable(self) -> ColumnElement[bool]:
        """Match rows this worker may claim now."""
        return reclaimable(self._claimed_at)

    async def stamp(self, rows: tuple[Any, ...], session: AsyncSession) -> Instant:
        """Take ownership of freshly selected rows and return the claim token."""
        claimed_at = Instant.now()
        for row in rows:
            self._claimed_at.__set__(row, claimed_at)
        await session.commit()
        return claimed_at

    async def complete(self, identity: tuple[ColumnElement[bool], ...], claimed_at: Instant) -> bool:
        """Delete an acknowledged row, if this worker still owns it."""
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(self._model).where(*identity, self._claimed_at == claimed_at)),
            )
            await session.commit()
            return bool(result.rowcount)

    async def fail(
        self,
        identity: tuple[ColumnElement[bool], ...],
        claimed_at: Instant,
        *,
        attempts: int,
        error: str,
        max_attempts: int,
    ) -> bool:
        """Release a failed row for retry, or drop it at the attempt ceiling.

        Returns whether the row was dropped.
        """
        attempts += 1
        claim_filter = (*identity, self._claimed_at == claimed_at)
        async with self._session_factory() as session:
            if attempts >= max_attempts:
                result = cast(
                    CursorResult[Any],
                    await session.execute(delete(self._model).where(*claim_filter)),
                )
                await session.commit()
                return bool(result.rowcount)
            await session.execute(
                update(self._model)
                .where(*claim_filter)
                .values(
                    attempts=attempts,
                    claimed_at=None,
                    last_error=error[:4000],
                    **{self._ready_at.key: func.now() + retry_delay(attempts)},
                )
            )
            await session.commit()
            return False
