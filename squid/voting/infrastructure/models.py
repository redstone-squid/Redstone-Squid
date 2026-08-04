"""SQLAlchemy voting models."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC
from squid.voting.domain import VoteChoiceLiteral, VoteSessionResultLiteral, VoteVisibility


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
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    votes: Mapped[list["Vote"]] = relationship(
        back_populates="vote_session", default_factory=list, lazy="selectin", init=False, repr=False
    )
    options: Mapped[list["VoteSessionOption"]] = relationship(
        back_populates="vote_session",
        default_factory=list,
        lazy="selectin",
        order_by="VoteSessionOption.position",
        init=False,
        repr=False,
    )


class VoteSessionOption(Base, kw_only=True):
    """A reaction option configured for one vote session."""

    __tablename__ = "vote_session_options"
    __table_args__ = (
        UniqueConstraint(
            "vote_session_id", "guild_id", "position", name="vote_session_options_vote_session_id_position_key"
        ),
        CheckConstraint("choice IN ('approve', 'deny', 'generic')", name="vote_session_options_choice_check"),
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
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("0"))
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    choice: Mapped[VoteChoiceLiteral] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="options", lazy="raise_on_sql", repr=False)


class BuildVoteSession(Base, kw_only=True):
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


class DeleteLogVoteSession(Base, kw_only=True):
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


class GenericVoteSession(Base, kw_only=True):
    """Metadata for a user-created generic poll."""

    __tablename__ = "generic_vote_sessions"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('anonymous_live', 'visible_live', 'anonymous_hidden')",
            name="generic_vote_sessions_visibility_check",
        ),
        Index("generic_vote_sessions_deadline_idx", "deadline"),
    )
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="RESTRICT"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[VoteVisibility] = mapped_column(Text, nullable=False)
    deadline: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    __table_args__ = (
        CheckConstraint(
            "weight > 0 AND weight != 'Infinity'::double precision AND weight != 'NaN'::double precision",
            name="votes_weight_check",
        ),
    )
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
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    option_id: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="votes", lazy="raise_on_sql", repr=False)


class GuildVoteEmoji(Base, kw_only=True):
    """One ordered emoji in a guild/session-kind preset."""

    __tablename__ = "guild_vote_emojis"
    __table_args__ = (
        UniqueConstraint("guild_id", "kind", "position", name="guild_vote_emojis_position_key"),
        CheckConstraint("kind IN ('build', 'delete_log', 'generic')"),
        CheckConstraint("choice IN ('approve', 'deny', 'generic')"),
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    choice: Mapped[VoteChoiceLiteral] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class GuildVoteRoleWeight(Base, kw_only=True):
    """A role multiplier scoped to one guild and session kind."""

    __tablename__ = "guild_vote_role_weights"
    __table_args__ = (
        CheckConstraint("kind IN ('build', 'delete_log', 'generic')"),
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="guild_vote_role_weights_multiplier_check",
        ),
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False)
