"""SQLAlchemy bot authorization models."""

from sqlalchemy import ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class GlobalAdministrator(Base):
    """An active bot-wide administrator grant."""

    __tablename__ = "global_administrators"

    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="global_administrators_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    """Internal account receiving application-wide access."""
    granted_by_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="global_administrators_granted_by_account_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    """Internal account that issued the grant."""
    granted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    """When the active grant was created."""
