"""SQLAlchemy model for durable Discord reconciliation work."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class DiscordSyncQueueItem(Base, kw_only=True):
    """A coalesced request to refresh one Discord-rendered resource; a row means its Discord posts are stale.

    Not an event log: at most one row exists per (resource_kind, source_key), written by the
    `INSERT ... ON CONFLICT DO UPDATE` triggers in `squid/persistence/postgres_entities.sql` and deleted on
    acknowledgement. Re-reading a row says what the resource should look like now, not what happened to it.
    """

    __tablename__ = "discord_sync_queue"
    __table_args__ = (
        CheckConstraint(
            "resource_kind IN ('build', 'vote_session', 'starboard_entry')",
            name="discord_sync_queue_resource_kind_check",
        ),
        CheckConstraint("action IN ('refresh', 'delete')", name="discord_sync_queue_action_check"),
        UniqueConstraint("resource_kind", "source_key", name="discord_sync_queue_resource_key"),
        Index(
            "discord_sync_queue_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    """What source_key identifies: build, vote_session or starboard_entry."""
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    """The resource's id as text; a starboard entry is '<starboard_id>:<origin_message_id>'."""
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'refresh'"))
    """'refresh' re-renders the resource's posts, 'delete' removes them."""
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """When this row next becomes claimable, and the only column backoff writes.

    Separate from enqueued_at so a repeatedly failing row keeps its place in FIFO order and still reports its true
    age to the queue-health gauges.
    """
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the current worker claimed the row; a claim older than the visibility timeout may be taken over."""
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Database-minted fence the claiming worker's acknowledgement must match.

    Nullable so a worker from an older release, which stamps only claimed_at, can still drain the queue.
    """
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Set when the row is abandoned after too many attempts; a dead row is never claimed again."""
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("nextval('discord_sync_generation_seq')"),
        default=None,
    )
    """A globally monotonic staleness token from a sequence, compared against a post's applied revision.

    Not a per-row counter: acknowledging deletes the row, so a counter would restart at 1 and could name a
    revision a post had already applied.
    """
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Failed drains of this row; reaching the drainer's ceiling sets dead_at."""
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    """Truncated text of the most recent failure."""
