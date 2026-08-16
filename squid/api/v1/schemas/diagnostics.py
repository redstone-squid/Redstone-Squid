"""Stored error report representations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from squid.core.errors import ErrorCode, JSONValue
from squid.diagnostics.domain import ErrorReport


class ErrorReportSummary(BaseModel):
    """One stored failure, without its traceback or logs."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    correlation_id: str
    occurred_at: datetime
    surface: str
    origin: str | None = None
    exception_type: str
    code: ErrorCode | None = None

    @classmethod
    def from_domain(cls, report: ErrorReport) -> ErrorReportSummary:
        return cls(
            reference=report.reference,
            correlation_id=report.correlation_id,
            occurred_at=report.occurred_at.to_stdlib(),
            surface=report.surface,
            origin=report.origin,
            exception_type=report.exception_type,
            code=report.error_code,
        )


class ErrorReportDetail(ErrorReportSummary):
    """One stored failure with everything kept about it.

    Only reachable with `diagnostics.error.read`: the message and traceback are the unredacted
    internals that every other surface deliberately withholds from the user who triggered them.
    """

    message: str
    traceback: str
    context: dict[str, JSONValue] = Field(default_factory=dict)
    log_tail: list[str] = Field(default_factory=list)
    matching_references: int = 1
    """How many unexpired reports share this reference.

    Normally one. The short reference is a 48-bit prefix rather than a key, so a reader has to be
    told when they may be looking at the wrong incident.
    """

    @classmethod
    def of(cls, report: ErrorReport, matches: int) -> ErrorReportDetail:
        return cls(
            reference=report.reference,
            correlation_id=report.correlation_id,
            occurred_at=report.occurred_at.to_stdlib(),
            surface=report.surface,
            origin=report.origin,
            exception_type=report.exception_type,
            code=report.error_code,
            message=report.message,
            traceback=report.traceback,
            context=dict(report.context),
            log_tail=list(report.log_tail),
            matching_references=matches,
        )
