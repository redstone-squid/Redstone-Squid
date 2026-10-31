"""SQLAlchemy models for bot-owned Discord posts."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class DiscordPost(Base, kw_only=True):
    """A Discord message the bot owns and keeps rendered for some resource.

    Holds only applied state. What the post *should* look like lives in the matching
    `discord_sync_queue` row, so staleness is `applied_revision < queue.generation`
    rather than a desired revision copied onto every post by a trigger.
    """

    __tablename__ = "discord_posts"
    __table_args__ = (
        CheckConstraint(
            "resource_kind IN ('build', 'vote_session', 'starboard_entry')",
            name="discord_posts_resource_kind_check",
        ),
        CheckConstraint(
            "surface IN ('build_card', 'build_review', 'vote_card', 'starboard_entry')",
            name="discord_posts_surface_check",
        ),
        CheckConstraint("applied_revision >= 0", name="discord_posts_applied_revision_check"),
        # The idempotency guarantee: one live post per resource per channel. Redelivery,
        # partial-failure retries and reconciliation all rely on this rather than on each
        # caller inventing its own "have I already posted here?" check.
        Index(
            "discord_posts_resource_channel_key",
            "resource_kind",
            "resource_key",
            "channel_id",
            unique=True,
            postgresql_where=text("suppressed_at IS NULL"),
        ),
        Index("discord_posts_resource_idx", "resource_kind", "resource_key"),
    )

    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", name="discord_posts_message_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Denormalised from `messages.channel_id`: a unique index cannot span a join."""
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    resource_key: Mapped[str] = mapped_column(Text, nullable=False)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    applied_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    """The queue generation this post was last rendered at."""
    posted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    rendered_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    suppressed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Set when the post was deleted outside the bot. Renderers choose whether to repost."""
