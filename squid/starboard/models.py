"""Store the reactions that qualify messages for a starboard channel.

A board's configuration selects source channels, accepted emojis, and thresholds.
OriginMessage holds facts about a source message; StarboardVote records each
member's weighted reaction; StarboardEntry holds their total for one board.
The same source message can have independent entries on several boards.

An entry's published Discord message is tracked separately as a DiscordPost.
Having an entry here does not imply that its score qualifies it for publication.
"""

from math import isfinite

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
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.messages.models import Message
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now
from squid.starboard.domain.models import StarboardDirection


class StarboardConfig(Base, kw_only=True):
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    required: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("3.0"), default=3.0)
    """Score at or above which an unposted entry qualifies for publication."""
    required_remove: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"), default=0.0)
    """Score at or below which an existing entry no longer qualifies for publication."""
    self_vote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    allow_bots: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Whether messages authored by bots may qualify; this does not authorize bot voters."""
    require_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    min_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    max_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Maximum source-message age in seconds; zero disables the upper age limit."""
    autoreact_upvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    autoreact_downvote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    remove_invalid_reactions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    link_edits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    link_deletes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    display_emoji: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'⭐'"), default="⭐")
    colour: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4415105"), default=0x435E81)
    jump_to_message: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    attachments_list: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    replied_to: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    ping_author: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )

    emojis: tuple[StarboardEmoji, ...] = ()
    """Configured reactions, ordered and populated by the repository."""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Check the configuration values before saving or using them."""
        if not self.name.strip():
            msg = tr(t"starboard-starboard-name-empty = Starboard name cannot be empty.")
            raise ValidationError(msg)
        if not isfinite(self.required) or not isfinite(self.required_remove):
            msg = tr(t"starboard-starboard-thresholds-finite = Starboard thresholds must be finite.")
            raise ValidationError(msg)
        if self.required <= self.required_remove:
            msg = tr(t"starboard-post-threshold-exceed = The post threshold must exceed the removal threshold.")
            raise ValidationError(msg)
        if self.min_age_seconds < 0 or self.max_age_seconds < 0:
            msg = tr(t"starboard-starboard-message-ages = Starboard message ages cannot be negative.")
            raise ValidationError(msg)
        if self.max_age_seconds and self.min_age_seconds > self.max_age_seconds:
            msg = tr(t"starboard-minimum-message-age = The minimum message age cannot exceed the maximum.")
            raise ValidationError(msg)
        if not 0 <= self.colour <= 0xFFFFFF:
            msg = tr(t"starboard-starboard-colour-valid = Starboard colour must be a valid RGB value.")
            raise ValidationError(msg)
        if not self.display_emoji.strip():
            msg = tr(t"starboard-starboard-display-emoji = Starboard display emoji cannot be empty.")
            raise ValidationError(msg)
        aliases = [item.emoji for item in self.emojis]
        if len(aliases) != len(set(aliases)):
            msg = tr(t"starboard-starboard-emojis-unique = Starboard emojis must be unique.")
            raise ValidationError(msg)


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
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True, default=None
    )
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    direction: Mapped[StarboardDirection] = mapped_column(Text, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Check the configuration values before saving or using them."""
        if not self.emoji.strip():
            msg = tr(t"starboard-starboard-emoji-empty = Starboard emoji cannot be empty.")
            raise ValidationError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = tr(
                t"starboard-starboard-emoji-multiplier = Starboard emoji "
                t"multiplier must be finite and greater than zero."
            )
            raise ValidationError(msg)
        if self.position < 0:
            msg = tr(t"starboard-starboard-emoji-position = Starboard emoji position cannot be negative.")
            raise ValidationError(msg)


class StarboardSource(Base, kw_only=True):
    """A guild or channel whose messages feed a starboard."""

    __tablename__ = "starboard_sources"
    __table_args__ = (Index("starboard_sources_guild_idx", "guild_id"),)
    starboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("starboards.id", ondelete="CASCADE"), primary_key=True
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("server_settings.server_id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("0"), default=0)
    """Source channel ID, or zero to include every channel in the source guild."""
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)


class OriginMessage(Base, kw_only=True):
    """A source message that has been evaluated by at least one starboard."""

    __tablename__ = "starboard_origin_messages"
    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    message: Mapped[Message] = relationship(lazy="raise")
    author_is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_nsfw: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )

    @property
    def guild_id(self) -> int:
        assert self.message.guild_id is not None
        return self.message.guild_id

    @property
    def channel_id(self) -> int:
        assert self.message.channel_id is not None
        return self.message.channel_id

    @property
    def author_id(self) -> int:
        return self.message.author_id

    @property
    def posted_at(self) -> Instant:
        assert self.message.created_at is not None
        return self.message.created_at

    @property
    def deleted_at(self) -> Instant | None:
        return self.message.deleted_at

    @property
    def present(self) -> bool:
        return not self.message.is_deleted


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
    direction: Mapped[StarboardDirection] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    target_author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class StarboardEntry(Base, kw_only=True):
    """Current vote totals and rendering bookkeeping for one source message on one starboard."""

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
