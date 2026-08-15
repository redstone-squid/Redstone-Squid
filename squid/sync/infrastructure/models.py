"""SQLAlchemy model for durable Discord reconciliation work."""

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class DiscordSyncQueueItem(Base, kw_only=True):
    """A coalesced request to refresh one Discord-rendered resource."""

    __tablename__ = "discord_sync_queue"
    __table_args__ = (
        CheckConstraint("resource_kind IN ('build', 'vote_session')", name="discord_sync_queue_resource_kind_check"),
        CheckConstraint("action IN ('refresh', 'delete')", name="discord_sync_queue_action_check"),
        UniqueConstraint("resource_kind", "source_key", name="discord_sync_queue_resource_key"),
        Index(
            "discord_sync_queue_ready_idx",
            "enqueued_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'refresh'"))
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("nextval('discord_sync_generation_seq')"),
        default=None,
    )
    """A globally monotonic staleness token, not a per-row counter.

    Acknowledging a job deletes its queue row, so a counter restarted at 1 on the next
    enqueue and could name a revision below one a post had already applied. Sequence
    values survive that because they are never rolled back or reused.
    """
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
