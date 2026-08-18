"""SQLAlchemy Discord message models."""

from sqlalchemy import BigInteger, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class Message(Base):
    """A Discord message the bot has seen.

    One row per Discord message, holding only what is true about the message itself.
    Why it matters is expressed by the tables that reference it: `discord_posts` for
    the messages the bot owns and renders, `build_source_messages` for the ones a
    build was inferred from.
    """

    __tablename__ = "messages"
    __table_args__ = (Index("messages_guild_idx", "guild_id"),)

    # `autoincrement=False` is what makes the docstring below true of the DDL as well: an
    # integer primary key is `SERIAL` by default, so without it the model asks for a sequence
    # the deployed database has never had. Left as a code comment rather than extending the
    # attribute docstring, because `Base` turns those into column comments and this is a note
    # about the mapping, not about the column.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    """The Discord snowflake. Never generated here; the message exists before the row."""
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("server_settings.server_id", name="public_messages_server_id_fkey", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    """The guild the message was sent in, or NULL in DMs."""
    content: Mapped[str | None] = mapped_column(Text, default=None)
    """Never exposed through the API. Retained for offline build inference and edit views."""
    created_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When Discord created the message, denormalised from the snowflake."""
    observed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """When the bot first recorded this message."""
    edited_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When `content` was last refreshed from a Discord edit."""
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Set when Discord reports the message gone. The row is a retained fact, never erased."""
