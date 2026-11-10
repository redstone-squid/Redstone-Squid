"""SQLAlchemy server settings models."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    # Discord assigns guild IDs. Disable autoincrement so SQLAlchemy does not
    # create a sequence for this integer primary key.
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    in_server: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    locale: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord's guild/user locale when unset."""


class RuntimeSetting(Base):
    """Singleton containing global defaults and owner-guild community routing."""

    __tablename__ = "runtime_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="runtime_settings_singleton"),
        CheckConstraint("welcome_forward_chance BETWEEN 0 AND 1", name="runtime_settings_chance"),
        CheckConstraint("notification_retention_days BETWEEN 1 AND 3650", name="runtime_settings_retention"),
        CheckConstraint(
            "build_vote_pass_threshold > 0 AND build_vote_fail_threshold < 0 "
            "AND delete_vote_pass_threshold > 0 AND delete_vote_fail_threshold < 0",
            name="runtime_settings_thresholds",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    welcome_forward_chance: Mapped[float] = mapped_column(Float, nullable=False)
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    redstoner_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    build_vote_pass_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    build_vote_fail_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delete_vote_pass_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delete_vote_fail_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    notification_retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    redstoner_starboard_author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redstoner_starboard_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redstoner_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redstoner_corner_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redstoner_announcement_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    welcome_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    welcome_relay_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_tracker_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    build_log_channel_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)


class RuntimeSettingChange(Base):
    """An administrator's committed change, including before and after snapshots."""

    __tablename__ = "runtime_setting_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    actor_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """Internal account that made the edit; retained as null if that account is deleted."""
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, init=False
    )
    before: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
