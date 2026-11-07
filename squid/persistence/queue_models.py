"""Shared persisted claim fields; queues opt into dead-letter timestamps separately."""

import uuid

from sqlalchemy import Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column
from whenever import Instant

from squid.persistence.types import InstantUTC, now


class QueueColumns(MappedAsDataclass, kw_only=True, eq=False):
    """Fields shared by token-fenced work queues."""

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class DeadLetterColumns(MappedAsDataclass, kw_only=True, eq=False):
    """Opt-in terminal marker; recompute queues deliberately retry indefinitely."""

    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
