"""Error report values and the failure that a reference does not resolve."""

from dataclasses import dataclass

from whenever import Date, Instant

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import tr

MAX_REFERENCE_LENGTH = 128
"""Longest reference a lookup will consider.

Bounded because a caller supplies it: `resolve_request_id` accepts an inbound `Request-Id` of up
to 128 characters and echoes it verbatim, so that is the widest value that can legitimately end
up stored under `correlation_id`.
"""


class ErrorReportNotFoundError(NotFoundError):
    """No stored report matches the reference a caller quoted.

    Expiry and a typo are deliberately indistinguishable here: both mean "nothing to show", and
    telling a caller that a reference *used* to exist reveals that an error happened.
    """

    default_message = tr(t"diagnostics-no-stored-error = No stored error matches that reference.")
    default_title = tr(t"diagnostics-error-report-not-found = Error report not found")
    default_resource = "error_report"


@dataclass(frozen=True, slots=True)
class ErrorReportFilters:
    """Which unexpired reports a listing returns. Every supplied filter must match."""

    surface: str | None = None
    error_code: ErrorCode | None = None
    occurred_after: Instant | None = None
    """Inclusive lower bound."""
    occurred_before: Instant | None = None
    """Exclusive upper bound."""
    work_lost_only: bool = False
    """Select only failures that permanently abandoned work, such as a dead-lettered job."""


EVERY_REPORT = ErrorReportFilters()


@dataclass(frozen=True, slots=True)
class ErrorReportDayCount:
    """How many unexpired reports one surface stored on one UTC day."""

    day: Date
    surface: str
    count: int
