"""Error report values and the failure that a reference does not resolve."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from whenever import Instant

from squid.core.errors import ErrorCode, JSONValue, NotFoundError
from squid.core.i18n import tr

MAX_REFERENCE_LENGTH = 128
"""Longest reference a lookup considers.

`resolve_request_id` echoes an inbound `Request-Id` of up to 128 characters verbatim, so that is the widest value
that can legitimately be stored as `correlation_id`.
"""


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """One unexpected failure, kept long enough for somebody to ask about it."""

    id: UUID
    correlation_id: str
    """The full ID, as it appears in logs and the `Request-Id` response header."""
    reference: str
    """The shortened form the user was shown and will be quoting."""
    occurred_at: Instant
    expires_at: Instant
    surface: str
    """Which transport failed: an application command, a view callback, a route, a worker job."""
    origin: str | None = None
    """The command name, route, or job the failure came from, when the surface knows it."""
    exception_type: str = ""
    message: str = ""
    error_code: ErrorCode | None = None
    traceback: str = ""
    context: Mapping[str, JSONValue] = field(default_factory=dict)
    """Redacted diagnostic context. Never contains stable Discord account identifiers."""
    log_tail: Sequence[str] = ()
    """What the process logged under this correlation ID before the failure, oldest first."""
    work_lost: bool = False
    """Whether this failure permanently abandoned work.

    A dead-lettered job is gone -- nothing retries it, and the search document or render it owed never appears --
    where a logged-and-recovered exception costs nothing.
    """


class ErrorReportNotFoundError(NotFoundError):
    """No stored report matches the reference a caller quoted.

    Expiry and a typo are deliberately indistinguishable: saying a reference once existed reveals that an error
    happened.
    """

    default_message = tr(t"No stored error matches that reference.")
    default_title = tr(t"Error report not found")
    default_resource = "error_report"
