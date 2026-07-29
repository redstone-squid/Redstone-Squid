"""SQLAlchemy server settings models."""

from sqlalchemy import ARRAY, BigInteger, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    staff_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=True, default_factory=list)
    trusted_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=True, default_factory=list)
    in_server: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
