"""SQLAlchemy starboard models."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class Starboard(Base, kw_only=True):
    """A named weighted-message board owned by one Discord guild."""

    __tablename__ = "starboards"
    __table_args__ = (
        Index("starboards_guild_name_key", "guild_id", func.lower(text("name")), unique=True),
        CheckConstraint("btrim(name) != ''", name="starboards_name_check"),
        CheckConstraint(
            "required > required_remove "
            "AND required != 'Infinity'::double precision "
            "AND required != '-Infinity'::double precision "
            "AND required != 'NaN'::double precision "
            "AND required_remove != 'Infinity'::double precision "
            "AND required_remove != '-Infinity'::double precision "
            "AND required_remove != 'NaN'::double precision",
            name="starboards_thresholds_check",
        ),
        CheckConstraint(
            "min_age_seconds >= 0 AND max_age_seconds >= 0 "
            "AND (max_age_seconds = 0 OR min_age_seconds <= max_age_seconds)",
            name="starboards_age_check",
        ),
        CheckConstraint("colour BETWEEN 0 AND 16777215", name="starboards_colour_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    required: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("3.0"), default=3.0)
    required_remove: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"), default=0.0)
    self_vote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    allow_bots: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    require_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    min_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    max_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    autoreact_upvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    autoreact_downvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    remove_invalid_reactions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    link_edits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    link_deletes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    display_emoji: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'⭐'"), default="⭐")
    colour: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("4415105"), default=0x435E81)
    jump_to_message: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    attachments_list: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    replied_to: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    ping_author: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class StarboardEmoji(Base, kw_only=True):
    """An ordered upvote or downvote emoji for a starboard."""

    __tablename__ = "starboard_emojis"
    __table_args__ = (
        CheckConstraint("btrim(emoji) != ''", name="starboard_emojis_emoji_check"),
        CheckConstraint("direction IN ('up', 'down')", name="starboard_emojis_direction_check"),
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="starboard_emojis_multiplier_check",
        ),
        CheckConstraint("position >= 0", name="starboard_emojis_position_check"),
        Index("starboard_emojis_position_key", "starboard_id", "position", unique=True),
    )

    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class StarboardSource(Base, kw_only=True):
    """A guild or channel whose messages feed a starboard."""

    __tablename__ = "starboard_sources"
    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("0"), default=0)
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)


class StarboardOriginMessage(Base, kw_only=True):
    """A source message that has been evaluated by at least one starboard."""

    __tablename__ = "starboard_origin_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_nsfw: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    posted_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)


class StarboardVote(Base, kw_only=True):
    """One member's current weighted reaction to one message on one starboard."""

    __tablename__ = "starboard_votes"
    __table_args__ = (
        CheckConstraint("direction IN ('up', 'down')", name="starboard_votes_direction_check"),
        CheckConstraint(
            "weight > 0 AND weight != 'Infinity'::double precision AND weight != 'NaN'::double precision",
            name="starboard_votes_weight_check",
        ),
        Index("starboard_votes_target_author_created_idx", "starboard_id", "target_author_id", "created_at"),
        Index("starboard_votes_origin_message_idx", "origin_message_id"),
    )

    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    origin_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboard_origin_messages.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    emoji: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    target_author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class StarboardEntry(Base, kw_only=True):
    """The materialized-post state for one source message on one starboard."""

    __tablename__ = "starboard_entries"
    __table_args__ = (Index("starboard_entries_score_idx", "starboard_id", text("score DESC")),)

    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    origin_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboard_origin_messages.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"), default=0.0)
    raw_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_rendered_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    first_posted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    updated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)


class StarboardRoleMultiplier(Base, kw_only=True):
    """A role multiplier scoped to one starboard."""

    __tablename__ = "starboard_role_multipliers"
    __table_args__ = (
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="starboard_role_multipliers_multiplier_check",
        ),
    )
    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False)
