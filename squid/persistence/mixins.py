"""Opt-in columns for common persistence metadata."""

from sqlalchemy import func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column
from whenever import Instant

from squid.persistence.types import InstantUTC


class CreatedAtMixin(MappedAsDataclass, kw_only=True, eq=False):
    """Creation time, available at construction and defaulted for direct database inserts."""

    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class AuditTimestampsMixin(CreatedAtMixin):
    """Creation and update times; the writer explicitly advances updated_at.

    These columns do not install update hooks or record change history. Callers can
    supply timestamps when importing records or recording a shared operation time.
    """

    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
