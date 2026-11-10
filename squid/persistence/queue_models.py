"""Shared persisted claim fields; queues opt into dead-letter timestamps separately."""

import uuid

from sqlalchemy import CheckConstraint, Integer, Table, Text, event, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column
from whenever import Instant

from squid.persistence.types import InstantUTC


class QueueColumns(MappedAsDataclass, kw_only=True, eq=False):
    """Fields shared by token-fenced work queues."""

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


@event.listens_for(QueueColumns, "instrument_class", propagate=True)
def _add_queue_checks(_mapper: object, cls: type) -> None:
    """Give every queue table the same claim and attempt checks.

    A per-class `__table_args__` would replace, not extend, a mixin's, so the checks are attached
    to each queue's table once declarative has built it.
    """
    table = cls.__table__  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(table, Table)
    table.append_constraint(CheckConstraint("attempts >= 0", name=f"{table.name}_attempts_nonnegative"))
    table.append_constraint(
        CheckConstraint("(claimed_at IS NULL) = (claim_token IS NULL)", name=f"{table.name}_claim_complete")
    )


class DeadLetterColumns(MappedAsDataclass, kw_only=True, eq=False):
    """Opt-in terminal marker; recompute queues deliberately retry indefinitely."""

    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
