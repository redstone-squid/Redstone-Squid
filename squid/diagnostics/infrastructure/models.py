"""SQLAlchemy model for stored error reports."""

import uuid

from sqlalchemy import Boolean, Index, Text, false, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class ErrorReport(Base, kw_only=True):
    """One unexpected failure, retained so the reference its user was shown can be resolved."""

    __tablename__ = "error_reports"
    __table_args__ = (
        Index("error_reports_reference_idx", "reference"),
        Index("error_reports_correlation_id_idx", "correlation_id"),
        Index("error_reports_expires_at_idx", "expires_at"),
        Index("error_reports_occurred_at_idx", "occurred_at"),
        # Partial: the listing that filters on it wants only the true rows, and they are the
        # rare ones. A full index would be mostly `false` entries nothing ever reads.
        Index("error_reports_work_lost_idx", "occurred_at", postgresql_where=text("work_lost")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    """The full correlation ID, as it appears in logs and the Request-Id response header."""
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    """The shortened form shown to the user, indexed because it is what they quote back.

    Not unique: it is a 48-bit prefix of the correlation ID, so a collision is possible even
    though it is vanishingly unlikely, and a unique constraint would turn that into a failure to
    record the second error rather than an ambiguous lookup.
    """
    occurred_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the failure was captured."""
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When retention drops this report."""
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    """Which transport failed: an application command, a view callback, a route, a worker job."""
    origin: Mapped[str | None] = mapped_column(Text, default=None)
    """The command name, route, or job the failure came from, when the surface knows it."""
    exception_type: Mapped[str] = mapped_column(Text, nullable=False)
    """Qualified name of the raised exception class."""
    message: Mapped[str] = mapped_column(Text, nullable=False)
    """The exception's own string form, which is never shown to the user who triggered it."""
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    """The application ErrorCode, when the failure carried one."""
    traceback: Mapped[str] = mapped_column(Text, nullable=False)
    """Rendered traceback, truncated from the front so the frames nearest the failure survive."""
    context: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default_factory=dict)
    """Redacted diagnostic context. Never contains stable Discord account identifiers."""
    log_tail: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default_factory=list)
    """What the process logged under this correlation ID before failing, oldest first."""
    work_lost: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)
    """Whether this failure permanently abandoned work, as a dead-lettered job does."""
