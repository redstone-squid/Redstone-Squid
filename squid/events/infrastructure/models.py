"""SQLAlchemy models for the append-only domain-event log."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, Integer, SmallInteger, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class DomainEventRecord(Base, kw_only=True):
    """One state transition, recorded once and never coalesced."""

    __tablename__ = "domain_events"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="domain_events_schema_version_positive"),
        Index("domain_events_aggregate_idx", "aggregate_kind", "aggregate_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), default=1)
    aggregate_kind: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    occurred_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


# Membership is data rather than trigger logic, so adding a consumer is an insert and
# the emitting trigger never has to learn who is listening.
class DomainEventConsumer(Base, kw_only=True):
    """A registered reader of the event log."""

    __tablename__ = "domain_event_consumers"

    name: Mapped[str] = mapped_column(Text, primary_key=True)


class DomainEventDeliveryRecord(Base, kw_only=True):
    """One consumer's outstanding delivery of one event."""

    __tablename__ = "domain_event_deliveries"
    __table_args__ = (
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
