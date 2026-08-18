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
from squid.persistence.types import InstantUTC, StrEnumText, now
from squid.voting.domain import PollScope, VoteChoice, VoteKind, VoteSessionResult, VoteStatus, VoteVisibility

_KIND_VALUES = ", ".join(f"'{kind.value}'" for kind in VoteKind)
_CHOICE_VALUES = ", ".join(f"'{choice.value}'" for choice in VoteChoice)
_STATUS_VALUES = ", ".join(f"'{status.value}'" for status in VoteStatus)
_RESULT_VALUES = ", ".join(f"'{result.value}'" for result in VoteSessionResult)
_VISIBILITY_VALUES = ", ".join(f"'{visibility.value}'" for visibility in VoteVisibility)

_SCOPE_VALUES = ", ".join(f"'{scope.value}'" for scope in PollScope)

THRESHOLD_CONSTRAINT = (
    "CASE WHEN kind = 'generic'"
    " THEN pass_threshold IS NULL AND fail_threshold IS NULL"
    " ELSE pass_threshold > 0 AND fail_threshold < 0"
    " END"
)
"""Thresholds belong to score-closing kinds only.

Generic polls close on a deadline, so a threshold on one is unreadable state; this
is the constraint that stopped the `32767`/`-32768` sentinels from coming back.
"""


class VoteSession(Base, kw_only=True):
    """A voting session for builds, log deletions, or generic polls."""

    __tablename__ = "vote_sessions"
    __table_args__ = (
        Index("vote_sessions_author_idx", "author_account_id"),
        CheckConstraint(THRESHOLD_CONSTRAINT, name="vote_sessions_threshold_kind_check"),
        CheckConstraint(f"kind = ANY (ARRAY[{_KIND_VALUES}])", name="vote_sessions_kind_check"),
        CheckConstraint(f"status = ANY (ARRAY[{_STATUS_VALUES}])", name="vote_sessions_status_check"),
        CheckConstraint(f"result = ANY (ARRAY[{_RESULT_VALUES}])", name="vote_sessions_result_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    status: Mapped[VoteStatus] = mapped_column(StrEnumText(VoteStatus), nullable=False)
    result: Mapped[VoteSessionResult] = mapped_column(
        StrEnumText(VoteSessionResult), nullable=False, server_default=text("'pending'::text")
    )
    """The result of the vote session."""
    author_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", name="vote_sessions_author_account_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[VoteKind] = mapped_column(StrEnumText(VoteKind), nullable=False)
    pass_threshold: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fail_threshold: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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


class VoteSessionOption(Base, kw_only=True):
    """A reaction option configured for one vote session."""

    __tablename__ = "vote_session_options"
    __table_args__ = (
        UniqueConstraint(
            "vote_session_id", "guild_id", "position", name="vote_session_options_vote_session_id_position_key"
        ),
        CheckConstraint(f"choice IN ({_CHOICE_VALUES})", name="vote_session_options_choice_check"),
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
    choice: Mapped[VoteChoice] = mapped_column(StrEnumText(VoteChoice), nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="options", lazy="raise_on_sql", repr=False)


class BuildVoteSession(Base, kw_only=True):
    __tablename__ = "build_vote_sessions"
    __table_args__ = (Index("build_vote_sessions_build_idx", "build_id"),)
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
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
        Index("generic_vote_sessions_guild_idx", "guild_id"),
        CheckConstraint(
            f"visibility IN ({_VISIBILITY_VALUES})",
            name="generic_vote_sessions_visibility_check",
        ),
        CheckConstraint(f"scope IN ({_SCOPE_VALUES})", name="generic_vote_sessions_scope_check"),
        CheckConstraint(
            "scope = 'guild' OR guild_id IS NOT NULL",
            name="generic_vote_sessions_network_guild_check",
        ),
        Index("generic_vote_sessions_deadline_idx", "deadline"),
    )
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    guild_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="RESTRICT"), nullable=True
    )
    """The guild whose emoji palette the poll was drafted against.

    Nullable so a poll can be created by a transport that has no guild -- the REST API
    or a standalone draft -- and have its presentation messages attached afterwards.
    """
    question: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[VoteVisibility] = mapped_column(StrEnumText(VoteVisibility), nullable=False)
    scope: Mapped[PollScope] = mapped_column(
        StrEnumText(PollScope), nullable=False, server_default=PollScope.GUILD.value
    )
    """Whether the poll is carded only in its own guild or in every vote channel."""
    deadline: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    __table_args__ = (
        Index("votes_account_idx", "account_id"),
        CheckConstraint(
            "weight > 0 AND weight != 'Infinity'::double precision AND weight != 'NaN'::double precision",
            name="votes_weight_check",
        ),
    )
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "vote_sessions.id",
            name="votes_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", name="votes_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
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
        CheckConstraint(f"kind IN ({_KIND_VALUES})"),
        CheckConstraint(f"choice IN ({_CHOICE_VALUES})"),
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[VoteKind] = mapped_column(StrEnumText(VoteKind), primary_key=True)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    choice: Mapped[VoteChoice] = mapped_column(StrEnumText(VoteChoice), nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class GuildVoteRoleWeight(Base, kw_only=True):
    """A role multiplier scoped to one guild and session kind."""

    __tablename__ = "guild_vote_role_weights"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_VALUES})"),
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="guild_vote_role_weights_multiplier_check",
        ),
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[VoteKind] = mapped_column(StrEnumText(VoteKind), primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False)
