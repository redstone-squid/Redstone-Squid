"""Reserve database work for a worker and record the outcome of its attempt.

Each queue repository describes its table with a QueueSpec and uses ClaimedRowQueue
for the shared lifecycle:

1. Claim ready rows, assigning each a timestamp and a new claim token.
2. Commit those claims before processing the work outside that transaction.
3. Use the token to complete the work or record a failure in another transaction.

A claim temporarily keeps other workers from selecting the same row. After the
visibility timeout, another worker may reclaim it with a new token. Completion
and failure only change rows whose stored token still matches; this check is the
claim fence. It prevents a slow worker from overwriting the newer worker's queue
state, but does not undo side effects the slow worker already performed elsewhere.

These helpers execute SQL through the supplied database session. The application
operation that called the queue repository is responsible for committing it.
"""

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    Interval,
    SQLColumnExpression,
    and_,
    delete,
    func,
    literal_column,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import FromClause
from whenever import Instant

from squid.observability import add_counter

logger = logging.getLogger(__name__)

VISIBILITY_TIMEOUT = timedelta(minutes=5)
"""How long a claim survives before another worker may reclaim it.

Workers can recover jobs left by a process that stopped. Handlers that take longer
than this timeout risk running at the same time as a worker that reclaims the job.
"""

BASE_RETRY_DELAY = timedelta(seconds=15)
MAX_RETRY_DELAY = timedelta(hours=1)

MAX_ERROR_LENGTH = 4000
"""How much of a failure message is kept, so one exception cannot bloat the row."""

_MAX_DOUBLING = 12
"""Where the backoff stops doubling.

At this exponent, the delay already exceeds `MAX_RETRY_DELAY`. Larger values
would not change the capped result, but could overflow Python's `timedelta` or
PostgreSQL's `power()`. Queues with unlimited retries can reach such counts.
"""

_ONE_SECOND = literal_column("interval '1 second'", type_=Interval())


def retry_delay(attempts: int) -> timedelta:
    """Back off exponentially, capped so even a stuck job still retries hourly."""
    delay = BASE_RETRY_DELAY * 2 ** min(attempts - 1, _MAX_DOUBLING)
    return min(delay, MAX_RETRY_DELAY)


def retry_delay_sql(attempts: SQLColumnExpression[int]) -> ColumnElement[timedelta]:
    """Express `retry_delay` against an attempts column.

    A batch can contain rows with different attempt counts, so calculate each
    delay in SQL. Keep this expression consistent with `retry_delay`.
    """
    doublings = func.least(attempts - 1, _MAX_DOUBLING)
    delay = _ONE_SECOND * (BASE_RETRY_DELAY.total_seconds() * func.power(2, doublings))
    return func.least(_ONE_SECOND * MAX_RETRY_DELAY.total_seconds(), delay)


@dataclass(frozen=True, slots=True)
class QueueHealthShape:
    """How one queue is aggregated into the queue-health union.

    Only `domain_events` needs this. Its deliveries are counted per registered
    consumer through an outer join, so a consumer with no outstanding rows still
    reports zero rather than vanishing from the metric. Every other queue counts
    its own table under a constant label and leaves this unset.
    """

    label: ColumnElement[str]
    source: FromClause
    group_by: tuple[SQLColumnExpression[Any], ...] = ()
    counted: SQLColumnExpression[Any] | None = None


class QueueSpec[ModelT]:
    """Describe how one queue table represents readiness, claims, and failed attempts.

    Queue repositories supply this description so ClaimedRowQueue can use the same
    SQL operations for tables with different column names and completion policies.
    Mapped column attributes keep their types available to the type checker. This
    is a plain class because a dataclass would read those descriptors through
    __get__, exposing the stored value's type instead of the column's type.

    Instances are shared module constants. Treat them as immutable.
    """

    __slots__ = (
        "attempts",
        "available_at",
        "claim_count",
        "claim_token",
        "claimed_at",
        "dead_at",
        "enqueued_at",
        "health",
        "key",
        "last_error",
        "model",
        "name",
        "pending",
    )

    def __init__(
        self,
        *,
        name: str,
        model: type[ModelT],
        key: tuple[InstrumentedAttribute[Any], ...],
        available_at: InstrumentedAttribute[Instant],
        claimed_at: InstrumentedAttribute[Instant | None],
        claim_token: InstrumentedAttribute[uuid.UUID | None],
        attempts: InstrumentedAttribute[int],
        last_error: InstrumentedAttribute[str | None],
        enqueued_at: InstrumentedAttribute[Instant] | None = None,
        dead_at: InstrumentedAttribute[Instant | None] | None = None,
        claim_count: InstrumentedAttribute[int] | None = None,
        pending: ColumnElement[bool] | None = None,
        health: QueueHealthShape | None = None,
    ) -> None:
        """Describe one queue table.

        Args:
            name: The label this queue reports under in metrics and logs.
            model: The mapped class, which the claim and acknowledgement target.
            key: The primary key, used to re-identify locked rows in the claiming UPDATE.
            available_at: The earliest time a worker may claim the row.
            claimed_at: When the current claim was taken, for the visibility timeout.
            claim_token: A token assigned on each claim and checked by acknowledgements.
            attempts: The recorded failure count, incremented when releasing a failed claim.
            last_error: Where a failure message is retained.
            enqueued_at: When the work was last requested, where that differs from `available_at`.
            dead_at: When retries stopped, or no column for queues without this marker.
            claim_count: Claims including reclaims, where a queue counts those separately.
            pending: An extra condition rows must meet before they can be claimed.
            health: How this queue is aggregated into the queue-health union.
        """
        if not key:
            msg = f"{name} needs at least one key column to fence on."
            raise ValueError(msg)
        self.name = name
        self.model = model
        self.key = key
        self.available_at = available_at
        self.claimed_at = claimed_at
        self.claim_token = claim_token
        self.attempts = attempts
        self.last_error = last_error
        self.enqueued_at = enqueued_at
        self.dead_at = dead_at
        self.claim_count = claim_count
        self.pending = pending
        self.health = health


@dataclass(frozen=True, slots=True)
class FenceOutcome:
    """Whether an acknowledgement matched the worker's claim token.

    When `applied` is false, no row was changed. Another worker may have reclaimed
    the row, or newer work may have replaced the claim. Callers must not treat
    that result as a successful acknowledgement.
    """

    applied: bool
    dead_lettered: bool = False


class ClaimedRowQueue[ModelT]:
    """Shared SQL operations that queue repositories use to claim work and record outcomes.

    Each method uses the supplied database session without committing it. QueueSpec
    supplies table-specific columns; the repository supplies any extra row filters
    and values needed for completion or permanent failure.
    """

    def __init__(self, spec: QueueSpec[ModelT]) -> None:
        self._spec = spec

    @property
    def spec(self) -> QueueSpec[ModelT]:
        return self._spec

    def reclaimable(self) -> ColumnElement[bool]:
        """Match rows that are unclaimed, or whose claim has expired."""
        claimed_at = self._spec.claimed_at
        return or_(claimed_at.is_(None), claimed_at < func.now() - VISIBILITY_TIMEOUT)

    def ready(self) -> ColumnElement[bool]:
        """Match rows a worker may claim right now."""
        conditions: list[ColumnElement[bool]] = [
            self._spec.available_at <= func.now(),
            self.reclaimable(),
        ]
        if self._spec.dead_at is not None:
            conditions.append(self._spec.dead_at.is_(None))
        if self._spec.pending is not None:
            conditions.append(self._spec.pending)
        return and_(*conditions)

    def held_by(self, token: uuid.UUID) -> ColumnElement[bool]:
        """Match a stored claim token; elapsed time alone does not invalidate it."""
        return self._spec.claim_token == token

    def held_by_any(self, tokens: Iterable[uuid.UUID]) -> ColumnElement[bool]:
        """Match rows that still carry one of the supplied claim tokens."""
        return self._spec.claim_token.in_(tuple(tokens))

    async def claim(
        self,
        *,
        limit: int,
        where: Sequence[ColumnElement[bool]] = (),
        session: AsyncSession,
    ) -> tuple[ModelT, ...]:
        """Claim up to `limit` ready rows, skipping rows locked by other transactions.

        Select by availability time and then primary key. Locking and assigning a
        new token happen in one statement, but the caller must commit the transaction
        to make the claims visible. PostgreSQL supplies the timestamp and tokens.
        """
        spec = self._spec
        candidates = (
            select(*spec.key)
            .where(self.ready(), *where)
            .order_by(spec.available_at, *spec.key)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        values: dict[str, Any] = {
            spec.claimed_at.key: func.now(),
            # Volatile, so PostgreSQL evaluates it once per row rather than once
            # per statement -- every row in a batch gets its own token for free.
            spec.claim_token.key: func.gen_random_uuid(),
        }
        if spec.claim_count is not None:
            values[spec.claim_count.key] = spec.claim_count + 1
        statement = (
            update(spec.model)
            .where(tuple_(*spec.key).in_(candidates))
            .values(**values)
            .returning(spec.model)
            .execution_options(synchronize_session=False)
        )
        return tuple((await session.scalars(statement)).all())

    def token_of(self, row: ModelT) -> uuid.UUID:
        """Return the token assigned by `claim`, raising if the row has none."""
        token = cast(uuid.UUID | None, getattr(row, self._spec.claim_token.key))
        if token is None:
            msg = f"A row claimed from {self._spec.name} came back without the token the claim minted."
            raise RuntimeError(msg)
        return token

    async def complete(
        self,
        identity: Sequence[ColumnElement[bool]],
        token: uuid.UUID,
        *,
        values: Mapping[str, Any] | None = None,
        session: AsyncSession,
    ) -> FenceOutcome:
        """Acknowledge a row this worker still owns.

        Delete the row unless values is supplied. With values, update the row and
        clear its claim; the repository must include any status that prevents the
        completed work from being claimed again. Schematic jobs use this form to
        retain their results instead of deleting the job.
        """
        spec = self._spec
        fence = (*identity, self.held_by(token))
        statement: Any
        if values is None:
            statement = delete(spec.model).where(*fence)
        else:
            statement = update(spec.model).where(*fence).values(**self._released(values))
        applied = bool(await self._rowcount(statement, session))
        if not applied:
            self._report_lost_fence("complete", token)
        return FenceOutcome(applied=applied)

    async def fail(
        self,
        identity: Sequence[ColumnElement[bool]],
        token: uuid.UUID,
        *,
        attempts: int,
        error: str,
        max_attempts: int | None,
        terminal: bool = False,
        values: Mapping[str, Any] | None = None,
        dead_values: Mapping[str, Any] | None = None,
        session: AsyncSession,
    ) -> FenceOutcome:
        """Record a failure and release the claim for retry or mark it terminal.

        `attempts` is the previous failure count; this call increments it. Passing
        `terminal=True` or reaching `max_attempts` selects `dead_values` and sets
        `dead_at` if the queue has that column. Otherwise use `values` and schedule
        a retry with backoff. With `max_attempts=None`, only `terminal` stops retries.

        A claim token mismatch leaves the row unchanged.
        """
        attempts += 1
        dead = terminal or (max_attempts is not None and attempts >= max_attempts)
        extra = dict(dead_values or {}) if dead else dict(values or {})
        released = self._failed_values(attempts, error=error, values=extra, dead=dead)
        statement = update(self._spec.model).where(*identity, self.held_by(token)).values(**released)
        applied = bool(await self._rowcount(statement, session))
        if not applied:
            # The stale worker cannot update the row, but its failure still needs a log.
            self._report_lost_fence("fail", token, error=error)
        return FenceOutcome(applied=applied, dead_lettered=dead and applied)

    async def complete_batch(
        self,
        tokens: Sequence[uuid.UUID],
        *,
        values: Mapping[str, Any] | None = None,
        session: AsyncSession,
    ) -> int:
        """Delete or update rows whose claim tokens still match, and return the row count.

        As with `complete`, supplying `values` updates rows and releases their claims.
        The count can be lower than the number of tokens if claims have been replaced
        or rows have already been removed. Duplicate tokens count only once.
        """
        if not tokens:
            return 0
        spec = self._spec
        statement: Any
        if values is None:
            statement = delete(spec.model).where(self.held_by_any(tokens))
        else:
            statement = update(spec.model).where(self.held_by_any(tokens)).values(**self._released(values))
        return await self._rowcount(statement, session)

    async def fail_batch(
        self,
        tokens: Sequence[uuid.UUID],
        *,
        error: str,
        session: AsyncSession,
    ) -> int:
        """Schedule retries for matching claim tokens and return the number of rows changed.

        Each row's failure count determines its retry delay. Rows whose tokens have
        been replaced are left alone, as in complete_batch.
        """
        if not tokens:
            return 0
        spec = self._spec
        # Rows claimed in one batch can have different failure counts, so the backoff has
        # to be computed per row inside the statement rather than passed in.
        released = self._failed_values(spec.attempts + 1, error=error)
        statement = update(spec.model).where(self.held_by_any(tokens)).values(**released)
        return await self._rowcount(statement, session)

    def _failed_values(
        self,
        attempts: int | ColumnElement[int],
        *,
        error: str,
        values: Mapping[str, Any] | None = None,
        dead: bool = False,
    ) -> dict[str, Any]:
        """Use the same incremented attempt count for persistence and retry timing."""
        spec = self._spec
        released = self._released({spec.attempts.key: attempts, **(values or {})}, error=error)
        if dead:
            if spec.dead_at is not None:
                released[spec.dead_at.key] = func.now()
        else:
            delay = retry_delay(attempts) if isinstance(attempts, int) else retry_delay_sql(attempts)
            released[spec.available_at.key] = func.now() + delay
        return released

    def _report_lost_fence(self, operation: str, token: uuid.UUID, *, error: str | None = None) -> None:
        """Log and count an acknowledgement rejected because its claim no longer matches."""
        add_counter("squid.queue.lost_fences", attributes={"squid.queue.name": self._spec.name})
        logger.warning(
            "Discarded a %s on the %s queue because the claim was already lost",
            operation,
            self._spec.name,
            extra={
                "squid.queue.name": self._spec.name,
                "squid.queue.claim_token": str(token),
                "squid.queue.discarded_error": error,
            },
        )

    def _released(self, values: Mapping[str, Any], *, error: str | None = None) -> dict[str, Any]:
        """Add the claim release, so no acknowledgement path can forget the token."""
        released = {
            **values,
            self._spec.claimed_at.key: None,
            self._spec.claim_token.key: None,
        }
        if error is not None:
            released[self._spec.last_error.key] = error[:MAX_ERROR_LENGTH]
        return released

    async def _rowcount(self, statement: Any, session: AsyncSession) -> int:
        result = cast(CursorResult[Any], await session.execute(statement))
        return result.rowcount
