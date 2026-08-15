"""SQLAlchemy tracked message models."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class Message(Base):
    """A message associated with a build or vote session."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "(projection_resource_kind IS NULL) = (projection_source_key IS NULL)",
            name="messages_projection_identity_complete",
        ),
        CheckConstraint(
            "projection_resource_kind IS NULL OR projection_resource_kind IN ('build', 'vote_session')",
            name="messages_projection_resource_kind_check",
        ),
        CheckConstraint(
            "desired_action IN ('refresh', 'delete')",
            name="messages_desired_action_check",
        ),
        CheckConstraint(
            "desired_revision > 0 AND applied_revision > 0 AND applied_revision <= desired_revision",
            name="messages_projection_revisions_valid",
        ),
        Index(
            "messages_projection_pending_idx",
            "desired_revision",
            postgresql_where=text("projection_resource_kind IS NOT NULL AND desired_revision > applied_revision"),
        ),
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True
    )  # init=True because this is the message ID, which should be known when creating the object
    server_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("server_settings.server_id", name="public_messages_server_id_fkey", ondelete="RESTRICT"),
        nullable=True,
    )
    """The guild the message was sent in, or NULL in DMs."""
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="Legacy tracking role; NULL for plain observed facts. Removed once every writer moves to discord_posts.",
    )
    content: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When Discord created the message. Denormalised from the snowflake so ordering needs no function."""
    observed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    """When the bot first recorded this message."""
    edited_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When `content` was last refreshed from a Discord edit."""
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Set when Discord reports the message gone. The row is a retained fact, never erased."""
    build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="public_messages_build_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    vote_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", name="messages_vote_session_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    projection_resource_kind: Mapped[str | None] = mapped_column(Text, default=None)
    projection_source_key: Mapped[str | None] = mapped_column(Text, default=None)
    desired_action: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'refresh'"), default="refresh"
    )
    desired_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    applied_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    updated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default_factory=Instant.now, onupdate=func.now())
    """When this row was last modified. Bumped automatically on every UPDATE."""
