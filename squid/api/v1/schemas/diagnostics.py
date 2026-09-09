"""Stored error report representations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from squid.core.errors import ErrorCode, JSONValue
from squid.diagnostics.domain import ErrorReport


class ErrorReportSummary(BaseModel):
    """One stored failure, without its traceback or logs."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(
        description="The short form the user was shown and quotes back. A 48-bit prefix rather than a key, so more "
        "than one unexpired report can share it."
    )
    correlation_id: str = Field(
        description="Full identifier of the failed request, as it appears in the `Request-Id` response header."
    )
    occurred_at: datetime
    surface: str = Field(
        description="Which transport failed: an application command, a view callback, a route, a worker job."
    )
    origin: str | None = Field(
        default=None, description="The command name, route or job it came from; null when the surface does not know."
    )
    exception_type: str
    code: ErrorCode | None = Field(
        default=None, description="Stable error code, null for a failure not raised as a typed `SquidError`."
    )
    work_lost: bool = Field(
        default=False,
        description="True when the failure permanently abandoned work, such as a dead-lettered job nothing retries. "
        "False for an exception that was logged and recovered from.",
    )

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
            work_lost=report.work_lost,
        )


class ErrorReportDetail(ErrorReportSummary):
    """One stored failure with everything kept about it.

    Only reachable with `diagnostics.error.read`: the message and traceback are the unredacted
    internals that every other surface deliberately withholds from the user who triggered them.
    """

    message: str
    traceback: str
    context: dict[str, JSONValue] = Field(
        default_factory=dict, description="Redacted diagnostic context; never carries stable Discord account ids."
    )
    log_tail: list[str] = Field(
        default_factory=list,
        description="What the process logged under this correlation id before the failure, oldest first.",
    )
    matching_references: int = Field(
        default=1,
        description="How many unexpired reports share this reference. Normally one; above one the report shown may "
        "be a different incident from the one the user meant.",
    )

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
            work_lost=report.work_lost,
            message=report.message,
            traceback=report.traceback,
            context=dict(report.context),
            log_tail=list(report.log_tail),
            matching_references=matches,
        )
