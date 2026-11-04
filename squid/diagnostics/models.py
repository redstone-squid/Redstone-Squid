"""SQLAlchemy model for stored error reports."""

import uuid

from sqlalchemy import Boolean, Index, Text, false, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.core.errors import ErrorCode, JSONValue
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText


class ErrorCodeText(StrEnumText[ErrorCode]):
    """Read unknown codes from older deployments as unclassified errors."""

    def process_result_value(self, value: str | None, dialect: Dialect) -> ErrorCode | None:
        try:
            return super().process_result_value(value, dialect)
        except ValueError:
            return None


class ErrorReport(Base, kw_only=True):
    """One unexpected failure, retained so the reference its user was shown can be resolved."""

    __tablename__ = "error_reports"
    __table_args__ = (
        Index("error_reports_reference_idx", "reference"),
        Index("error_reports_correlation_id_idx", "correlation_id"),
        Index("error_reports_expires_at_idx", "expires_at"),
        Index("error_reports_occurred_at_idx", "occurred_at"),
        # The work-lost listing needs only marked reports, ordered by occurrence time.
        Index("error_reports_work_lost_idx", "occurred_at", postgresql_where=text("work_lost")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    """The full correlation ID, as it appears in logs and the Request-Id response header."""
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    """Short reference shown to the user, usually the first 12 correlation-ID characters.

    References are not unique. Multiple failures can share a correlation ID, and
    different IDs can share a prefix; lookup also returns a match count.
    """
    occurred_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the failure was captured."""
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the report stops appearing in lookups and becomes eligible for deletion."""
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    """Which transport failed: an application command, a view callback, a route, a worker job."""
    origin: Mapped[str | None] = mapped_column(Text, default=None)
    """The command name, route, or job the failure came from, when the surface knows it."""
    exception_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Qualified name of the raised exception class."""
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """The exception's string form, available to callers authorized to read diagnostic details."""
    error_code: Mapped[ErrorCode | None] = mapped_column(ErrorCodeText(ErrorCode), default=None)
    """The application ErrorCode, when the failure carried one."""
    traceback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Rendered traceback, truncated from the front so the frames nearest the failure survive."""
    context: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, nullable=False, default_factory=dict)
    """Diagnostic context supplied by the caller; the storage service does not redact it."""
    log_tail: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default_factory=list)
    """What the process logged under this correlation ID before failing, oldest first."""
    work_lost: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)
    """Whether this failure permanently abandoned work, as a dead-lettered job does."""
