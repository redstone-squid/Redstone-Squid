"""SQLAlchemy tracked message models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from squid.persistence.base import Base

if TYPE_CHECKING:
    from squid.builds.infrastructure.models import Build
    from squid.voting.infrastructure.models import VoteSession


class Message(Base):
    """A message associated with a build or vote session."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True
    )  # init=True because this is the message ID, which should be known when creating the object
    server_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("server_settings.server_id", name="public_messages_server_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(
        Text, nullable=False, comment="The reason why the message is stored in the database"
    )
    content: Mapped[str | None] = mapped_column(Text)
    build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="public_messages_build_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    vote_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", name="messages_vote_session_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=func.now(), onupdate=func.now()
    )
    """When this row was last modified. Bumped automatically on every UPDATE."""

    build: Mapped[Build | None] = relationship(
        back_populates="messages", foreign_keys="Message.build_id", default=None, lazy="raise_on_sql"
    )
    vote_session: Mapped[VoteSession | None] = relationship(
        back_populates="messages", default=None, lazy="raise_on_sql"
    )
