"""Discord messages the bot publishes and updates."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC
from squid.posts.domain import ResourceKind, Surface


class DiscordPost(Base, kw_only=True):
    """A bot-owned Discord message displaying a build, vote, or other resource."""

    __tablename__ = "discord_posts"
    __table_args__ = (
        CheckConstraint(
            "resource_kind IN ('build', 'vote_session', 'starboard_entry', 'inference_run', 'submission_draft')",
            name="discord_posts_resource_kind_check",
        ),
        CheckConstraint(
            "surface IN ('build_card', 'build_review', 'vote_card', 'starboard_entry', 'submission_status')",
            name="discord_posts_surface_check",
        ),
        CheckConstraint("applied_revision >= 0", name="discord_posts_applied_revision_check"),
        # Suppressed posts leave room for a replacement in the same channel.
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
    """Copied from the message so the unique index can limit live posts per channel."""
    resource_kind: Mapped[ResourceKind] = mapped_column(Text, nullable=False)
    """The type of resource displayed by this message, used to select its renderer."""
    resource_key: Mapped[str] = mapped_column(Text, nullable=False)
    """The resource's identifier, such as a build ID or a starboard/message ID pair."""
    surface: Mapped[Surface] = mapped_column(Text, nullable=False)
    """The presentation to use, such as a build card or a vote card."""
    applied_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    """The last rendered queue generation; a larger queued generation means an update is pending."""
    posted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    rendered_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When a rendered update was last recorded; None until the first update."""
    suppressed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When an external deletion was recorded. Each renderer decides whether to publish a replacement."""

    @property
    def is_live(self) -> bool:
        """Whether this post has not been suppressed."""
        return self.suppressed_at is None
