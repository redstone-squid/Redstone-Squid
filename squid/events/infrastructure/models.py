"""SQLAlchemy models for the append-only domain-event log."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, Integer, SmallInteger, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class DomainEventRecord(Base, kw_only=True):
    """One state transition, recorded once and never coalesced."""

    __tablename__ = "domain_events"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="domain_events_schema_version_positive"),
        Index("domain_events_aggregate_idx", "aggregate_kind", "aggregate_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    """Dotted name, such as build.confirmed, that consumers dispatch on."""
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), default=1)
    """Version of the payload shape; a consumer that does not accept it dead-letters its delivery."""
    aggregate_kind: Mapped[str] = mapped_column(Text, nullable=False)
    """What aggregate_id identifies, such as build or vote_session."""
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    """The transition's own data, read according to schema_version."""
    occurred_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


# Membership is data rather than trigger logic, so adding a consumer is an insert and
# the emitting trigger never has to learn who is listening.
class DomainEventConsumer(Base, kw_only=True):
    """A registered reader of the event log; inserting a row is what makes later events fan out to that consumer."""

    __tablename__ = "domain_event_consumers"

    name: Mapped[str] = mapped_column(Text, primary_key=True)


class DomainEventDeliveryRecord(Base, kw_only=True):
    """One consumer's outstanding delivery of one event."""

    __tablename__ = "domain_event_deliveries"
    __table_args__ = (
        CheckConstraint("claim_count >= 0", name="domain_event_deliveries_claim_count_nonnegative"),
        CheckConstraint(
            "(claimed_at IS NULL) = (claim_token IS NULL)",
            name="domain_event_deliveries_claim_complete",
        ),
        Index(
            "domain_event_deliveries_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domain_events.id", ondelete="CASCADE"), primary_key=True
    )
    consumer: Mapped[str] = mapped_column(
        Text, ForeignKey("domain_event_consumers.name", ondelete="CASCADE"), primary_key=True
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Earliest time this delivery may be claimed; a failure pushes it out by the queue's backoff."""
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the current worker claimed it; a claim older than the visibility timeout may be taken over."""
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Fences the acknowledgement: a worker whose claim was taken over cannot complete or fail the row."""
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """How often the row has been claimed, including takeovers, which attempts does not count."""
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Set when the delivery is abandoned; a dead row is never claimed again."""
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Failed handler runs; reaching the consumer's ceiling sets dead_at."""
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    """Truncated text of the most recent failure."""
