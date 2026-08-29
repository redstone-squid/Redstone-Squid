"""The claim protocol shared by the durable work tables.

Seven tables in this codebase are work queues, and they all do the same three
things: claim a bounded batch of ready rows without two workers taking the same
row, hold that claim long enough to survive a process death, then acknowledge,
retry with backoff, or dead-letter.

What is shared here is the **claim** half -- the readiness predicate, the
database-minted fencing token, the backoff and the dead-letter policy. The
**acknowledgement** half is allowed to vary, because it genuinely does: some
queues delete the acknowledged row, `schematic_jobs` retains it with terminal
values, and `record_recompute_queue` leases whole scopes and acknowledges a set
of them at once. Splitting it there is what lets all seven use this; the previous
split, which shared acknowledgement and left every caller to hand-write the
select, was small enough that two adapters re-implemented the fence anyway.

Both clocks and the token come from PostgreSQL, never from the worker. A claim
stamps `now()` and `gen_random_uuid()` in the same statement that locks the rows,
so there is no window in which a row is selected but unstamped, no dependence on
worker clock skew against the database's, and -- because `gen_random_uuid()` is
volatile and therefore evaluated per row -- no chance of two rows in one batch
sharing a token.
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import FromClause
from whenever import Instant

from squid.observability import add_counter

logger = logging.getLogger(__name__)

VISIBILITY_TIMEOUT = timedelta(minutes=5)
"""How long a claim survives before another worker may reclaim it.

This is what recovers work from a process that died mid-job, so it has to exceed the
slowest realistic handler while staying short enough that a crash is not visible for
long.
"""

BASE_RETRY_DELAY = timedelta(seconds=15)
MAX_RETRY_DELAY = timedelta(hours=1)

MAX_ERROR_LENGTH = 4000
"""How much of a failure message is kept, so one exception cannot bloat the row."""

_MAX_DOUBLING = 12
"""Where the backoff stops doubling.

`BASE_RETRY_DELAY * 2 ** 12` already exceeds `MAX_RETRY_DELAY`, so clamping the
exponent cannot change the answer. It is what keeps both encodings defined at a
runaway attempt count: unclamped, the Python one raises `OverflowError` once the
product leaves `timedelta`'s range, and the SQL one drives `power()` to infinity.
A queue with `max_attempts=None` can reach those counts.
"""

_ONE_SECOND = literal_column("interval '1 second'", type_=Interval())


def retry_delay(attempts: int) -> timedelta:
    """Back off exponentially, capped so even a stuck job still retries hourly."""
    delay = BASE_RETRY_DELAY * 2 ** min(attempts - 1, _MAX_DOUBLING)
    return min(delay, MAX_RETRY_DELAY)


def retry_delay_sql(attempts: SQLColumnExpression[int]) -> ColumnElement[timedelta]:
    """Express `retry_delay` against an attempts column.

    Releasing a leased set covers rows with different attempt counts in one
    statement, so the policy has to exist as an expression as well as a function.
    `tests/integration/persistence/test_claimed_row_queue.py` pins the two
    encodings equal, which is the only thing keeping them from drifting apart.
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
    """One durable work table, described once for every caller that touches it.

    Column identity travels as `InstrumentedAttribute` rather than as a name,
    because these tables disagree on their spelling -- three call the claim column
    `locked_at` and three call it `claimed_at` -- and a name reaches the database
    before anything notices it is wrong. That typing is the whole point, which is
    why this is a plain class rather than a dataclass: a dataclass field holding a
    descriptor is read back through `__get__`, so a type checker sees `Instant`
    where the column belongs and stops catching the mistake.

    Instances are module-level constants and are never mutated; `__slots__` keeps
    a typo from quietly adding a field that nothing reads.
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
            available_at: The retry clock, and the only column backoff ever writes.
            claimed_at: When the current claim was taken, for the visibility timeout.
            claim_token: The database-minted fence every acknowledgement matches on.
            attempts: How many times the work has been tried.
            last_error: Where a failure message is retained.
            enqueued_at: When the work was last requested, where that differs from `available_at`.
            dead_at: Absent on a queue that must never stop retrying.
            claim_count: Claims including reclaims, where a queue counts those separately.
            pending: An extra readiness condition. Only `schematic_jobs` needs one.
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
    """What an acknowledgement actually did.

    `applied=False` means the claim was lost -- reclaimed after the visibility
    timeout, or re-enqueued underneath a slow worker. That is never nothing: it
    means the queue has begun doing this row's work twice, so callers log and count
    it rather than discarding it.
    """

    applied: bool
    dead_lettered: bool = False


class ClaimedRowQueue[ModelT]:
    """Claim and acknowledge rows of one work table.

    Constructed without a `session_factory`, every method requires an explicit
    `session=` and joins the caller's transaction instead of committing. That is
    what lets an adapter which hands live ORM rows to its caller -- the search
    projector -- share this protocol; the previous version committed a session it
    did not own, which both dropped the caller's row locks and excluded the one
    caller that had a transaction worth joining.
    """

    def __init__(
        self,
        spec: QueueSpec[ModelT],
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._spec = spec
        self._session_factory = session_factory

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
        """Match the row this worker still owns, and no other."""
        return self._spec.claim_token == token

    def held_by_any(self, tokens: Iterable[uuid.UUID]) -> ColumnElement[bool]:
        """Match the rows in a leased set that this worker still owns."""
        return self._spec.claim_token.in_(tuple(tokens))

    async def claim(
        self,
        *,
        limit: int,
        where: Sequence[ColumnElement[bool]] = (),
        session: AsyncSession | None = None,
    ) -> tuple[ModelT, ...]:
        """Take ownership of up to `limit` ready rows and return them.

        The lock and the stamp are one statement, so a row is never selected-but-
        unstamped, and both the timestamp and the token are the database's.
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
        if session is not None:
            return tuple((await session.scalars(statement)).all())
        async with self._require_factory().begin() as owned:
            return tuple((await owned.scalars(statement)).all())

    def token_of(self, row: ModelT) -> uuid.UUID:
        """Read the fence off a row `claim` just returned.

        The column is nullable only for the deploy window in which code from the
        previous release can still stamp a claim without one; a row this process
        claimed always has it.
        """
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
        session: AsyncSession | None = None,
    ) -> FenceOutcome:
        """Acknowledge a row this worker still owns.

        Deletes it, or -- when `values` is given -- updates it with terminal state
        and releases the claim, which is the shape `schematic_jobs` needs.
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
        session: AsyncSession | None = None,
    ) -> FenceOutcome:
        """Release a failed row for retry, or dead-letter it.

        `max_attempts=None` means this queue never stops retrying. That is only
        correct where a permanently stuck row is louder than a silently dropped
        one, since the row can then spin forever on a poisoned input.
        """
        attempts += 1
        dead = terminal or (max_attempts is not None and attempts >= max_attempts)
        extra = dict(dead_values or {}) if dead else dict(values or {})
        released = self._released({self._spec.attempts.key: attempts, **extra}, error=error)
        if dead:
            if self._spec.dead_at is not None:
                released[self._spec.dead_at.key] = func.now()
        else:
            released[self._spec.available_at.key] = func.now() + retry_delay(attempts)
        statement = update(self._spec.model).where(*identity, self.held_by(token)).values(**released)
        applied = bool(await self._rowcount(statement, session))
        if not applied:
            # The attempts increment and the error text are lost with the claim.
            # Silence here is what made a queue doing its work twice look healthy.
            self._report_lost_fence("fail", token, error=error)
        return FenceOutcome(applied=applied, dead_lettered=dead and applied)

    async def complete_batch(
        self,
        tokens: Sequence[uuid.UUID],
        *,
        values: Mapping[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """Acknowledge every row of a leased set this worker still owns.

        Returns how many rows the fence matched, which is below `len(tokens)`
        exactly when part of the lease was taken away mid-run.
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
        session: AsyncSession | None = None,
    ) -> int:
        """Release every row of a leased set for retry, each on its own backoff."""
        if not tokens:
            return 0
        spec = self._spec
        attempts = spec.attempts + 1
        released = self._released({spec.attempts.key: attempts}, error=error)
        # Rows in one lease can be on different attempt counts, so the backoff has
        # to be computed per row inside the statement rather than passed in.
        released[spec.available_at.key] = func.now() + retry_delay_sql(spec.attempts)
        statement = update(spec.model).where(self.held_by_any(tokens)).values(**released)
        return await self._rowcount(statement, session)

    def _report_lost_fence(self, operation: str, token: uuid.UUID, *, error: str | None = None) -> None:
        """Make a claim lost underneath a worker visible.

        A queue whose acknowledgements stop matching has begun doing some row's
        work twice, and it looks exactly like a healthy one from the outside.
        """
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

    async def _rowcount(self, statement: Any, session: AsyncSession | None) -> int:
        if session is not None:
            result = cast(CursorResult[Any], await session.execute(statement))
            return result.rowcount
        async with self._require_factory().begin() as owned:
            result = cast(CursorResult[Any], await owned.execute(statement))
            return result.rowcount

    def _require_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            msg = f"The {self._spec.name} queue was built without a session factory, so it needs an explicit session."
            raise RuntimeError(msg)
        return self._session_factory
