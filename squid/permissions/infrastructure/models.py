"""SQLAlchemy bot authorization models."""

from sqlalchemy import BigInteger, func
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class GlobalAdministrator(Base):
    """An active bot-wide administrator grant."""

    __tablename__ = "global_administrators"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    """Discord snowflake ID receiving bot-wide access."""
    granted_by_discord_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Discord snowflake ID of the owner who issued the grant."""
    granted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    """When the active grant was created."""
