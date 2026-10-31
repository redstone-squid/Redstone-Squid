"""SQLAlchemy server settings models."""

from sqlalchemy import BigInteger, Boolean, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    # The Discord guild snowflake, assigned by Discord and never minted here.
    # `autoincrement=False` keeps the DDL honest: an integer primary key is `SERIAL` by
    # default, which would attach a sequence the deployed database does not have. A code
    # comment rather than an attribute docstring, since `Base` turns those into column
    # comments and adding one here would be a schema change.
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    in_server: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    locale: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord's guild/user locale when unset."""
