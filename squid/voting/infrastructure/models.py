"""SQLAlchemy voting models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from squid.persistence.base import Base
from squid.voting.domain import VoteChoiceLiteral, VoteSessionResultLiteral

if TYPE_CHECKING:
    from squid.builds.infrastructure.models import Build
    from squid.messages.infrastructure.models import Message


class VoteSession(Base, kw_only=True):
    """A voting session for builds or log deletions."""

    __tablename__ = "vote_sessions"
    __table_args__ = (
        CheckConstraint("fail_threshold < 0", name="vote_sessions_fail_threshold_check"),
        CheckConstraint("pass_threshold > 0", name="vote_sessions_pass_threshold_check"),
        CheckConstraint(
            "result = ANY (ARRAY['approved', 'denied', 'cancelled', 'pending'])",
            name="vote_sessions_result_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[VoteSessionResultLiteral] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'::text"),
        comment="The result of the vote session.",
    )
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fail_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="vote_session", default_factory=list, lazy="selectin", init=False, repr=False
    )
    votes: Mapped[list[Vote]] = relationship(
        back_populates="vote_session", default_factory=list, lazy="selectin", init=False, repr=False
    )
    options: Mapped[list[VoteSessionOption]] = relationship(
        back_populates="vote_session",
        default_factory=list,
        lazy="selectin",
        order_by="VoteSessionOption.position",
        init=False,
        repr=False,
    )

    __mapper_args__ = {"polymorphic_on": kind}


class VoteSessionOption(Base, kw_only=True):
    """A reaction option configured for one vote session."""

    __tablename__ = "vote_session_options"
    __table_args__ = (
        UniqueConstraint("vote_session_id", "position", name="vote_session_options_vote_session_id_position_key"),
        CheckConstraint("choice IN ('approve', 'deny')", name="vote_session_options_choice_check"),
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="vote_session_options_multiplier_check",
        ),
        CheckConstraint("position >= 0", name="vote_session_options_position_check"),
        {"comment": "Ordered reaction options and positive weight multipliers captured for each vote session."},
    )

    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "vote_sessions.id",
            name="vote_session_options_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    choice: Mapped[VoteChoiceLiteral] = mapped_column(Text, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="options", lazy="raise_on_sql", repr=False)


class BuildVoteSession(VoteSession, kw_only=True):
    """Association table between builds and vote sessions."""

    __tablename__ = "build_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="build_vote_sessions_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "builds.id",
            name="build_vote_sessions_build_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    build: Mapped[Build] = relationship(back_populates="build_vote_sessions", lazy="joined")

    __mapper_args__ = {"polymorphic_identity": "build"}


class DeleteLogVoteSession(VoteSession, kw_only=True):
    """Association table between vote sessions and messages to be deleted."""

    __tablename__ = "delete_log_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="delete_log_vote_sessions_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
        primary_key=True,
    )
    target_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __mapper_args__ = {"polymorphic_identity": "delete_log"}


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="votes_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weight: Mapped[float] = mapped_column(Float, nullable=True)  # FIXME: Shouldn't be nullable

    vote_session: Mapped[VoteSession] = relationship(back_populates="votes", lazy="raise_on_sql", repr=False)
