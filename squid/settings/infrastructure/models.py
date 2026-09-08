"""SQLAlchemy server settings models."""

from sqlalchemy import BigInteger, Boolean, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class ServerSetting(Base):
    """One Discord guild's configuration; the row outlives the bot's membership of the guild."""

    __tablename__ = "server_settings"
    # The Discord guild snowflake, assigned by Discord and never minted here.
    # `autoincrement=False` keeps the DDL honest: an integer primary key is `SERIAL` by
    # default, which would attach a sequence the deployed database does not have. A code
    # comment rather than an attribute docstring, since `Base` turns those into column
    # comments and adding one here would be a schema change.
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    """Where smallest-record announcements go; null when unset, and no two guilds may share a channel."""
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    """Where fastest-record announcements go; null when unset, and no two guilds may share a channel."""
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    """Where first-record announcements go; null when unset, and no two guilds may share a channel."""
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """Where confirmed build cards are posted; null when unset."""
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """Where vote sessions are posted; null when unset."""
    in_server: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    """False once the bot leaves the guild; the settings are kept so a rejoin restores them."""
    locale: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord's guild/user locale when unset."""
