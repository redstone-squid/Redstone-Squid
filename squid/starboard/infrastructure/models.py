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
from squid.persistence.types import InstantUTC, now


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
    """The Discord channel the board posts into, not a channel it reads; sources are in starboard_sources."""
    name: Mapped[str] = mapped_column(Text, nullable=False)
    """Unique per guild, case-insensitively."""
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    required: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("3.0"), default=3.0)
    """Weighted score at or above which an entry is posted."""
    required_remove: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"), default=0.0)
    """Weighted score at or below which a post is removed; between the two thresholds the post state is unchanged."""
    self_vote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    allow_bots: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    require_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    min_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Votes on messages younger than this are rejected; 0 accepts any age."""
    max_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Votes on messages older than this are rejected; 0 means no upper limit."""
    autoreact_upvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    autoreact_downvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    remove_invalid_reactions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    link_edits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    link_deletes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    display_emoji: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'⭐'"), default="⭐")
    colour: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4415105"), default=0x435E81)
    """Embed accent as a packed 24-bit RGB integer."""
    jump_to_message: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    attachments_list: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    replied_to: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    ping_author: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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
    """'up' adds the vote's weight to the score, 'down' subtracts it."""
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    """Scales a vote cast with this emoji, on top of the voter's role multiplier."""
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """Display order within the board; unique per starboard."""


class StarboardSource(Base, kw_only=True):
    """A grant letting one channel's messages, or a whole guild's, feed a starboard."""

    __tablename__ = "starboard_sources"
    __table_args__ = (Index("starboard_sources_guild_idx", "guild_id"),)
    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("0"), default=0)
    """The single source channel, or 0 for every channel in the guild."""
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """Discord user who approved the grant; null for grants created with the board."""
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When the grant was approved; set together with approved_by."""


class StarboardOriginMessage(Base, kw_only=True):
    """A source message a starboard has evaluated; the id is the Discord message id."""

    __tablename__ = "starboard_origin_messages"
    __table_args__ = (Index("starboard_origin_messages_guild_idx", "guild_id"),)
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
    """When the source message was sent, against which the board's age window is measured."""
    seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Last time a vote refreshed this row's copy of the message's facts."""
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """Set when the source message is gone; cleared again if it turns out to still exist."""


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
    """'up' adds weight to the entry score, 'down' subtracts it."""
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    """Emoji multiplier times the voter's role multiplier, frozen at the time of the vote."""
    target_author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """The origin message's author, copied here so per-author vote totals need no join."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class StarboardEntry(Base, kw_only=True):
    """The current score of one source message on one starboard, and what its post last showed."""

    __tablename__ = "starboard_entries"
    __table_args__ = (
        Index("starboard_entries_origin_message_idx", "origin_message_id"),
        Index("starboard_entries_score_idx", "starboard_id", text("score DESC")),
    )

    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    origin_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboard_origin_messages.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"), default=0.0)
    """Sum of upvote weights minus downvote weights, recomputed from starboard_votes."""
    raw_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Number of voters, in either direction and unweighted."""
    last_rendered_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    """Score the live post shows; a difference from score is what marks the entry as needing a re-render."""
    first_posted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When the entry first reached the board. No code writes it, so rows created now stay null."""
    updated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When last_rendered_score was last written."""


class StarboardRoleMultiplier(Base, kw_only=True):
    """The weight a Discord role gives its members' votes on one starboard; roles with no row weigh 1."""

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
