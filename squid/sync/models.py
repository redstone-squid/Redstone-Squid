"""SQLAlchemy model for durable Discord reconciliation work."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class DiscordSyncQueueItem(Base, kw_only=True):
    """A pending refresh or deletion of a resource's Discord posts.

    Database triggers insert one row per resource and replace its action and
    generation when another change arrives. They also clear any existing claim,
    so a worker handling the older generation cannot acknowledge the new request.
    Workers read the resource's current state and delete the row on completion.

    The generation is compared with each post's applied revision. Several changes
    can share one pending row; this table does not retain their history.
    """

    __tablename__ = "discord_sync_queue"
    __table_args__ = (
        CheckConstraint(
            "resource_kind IN ('build', 'vote_session', 'starboard_entry', 'inference_run', 'submission_draft')",
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
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'refresh'"))
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Earliest time a worker may claim this row.

    Retries move this time forward without changing enqueued_at, preserving queue
    order and the age reported by health metrics.
    """
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Token generated for the current claim.

    Completion and failure updates require a matching token. Enqueuing another
    update clears it, preventing an older worker from acknowledging the new work.
    """
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("nextval('discord_sync_generation_seq')"),
        default=None,
    )
    """Sequence number used to compare requested work with a post's applied revision.

    The sequence continues after a queue row is deleted, so the next request does
    not restart at a revision the post has already rendered.
    """
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
