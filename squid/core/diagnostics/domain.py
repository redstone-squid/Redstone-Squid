"""Error report values and the failure that a reference does not resolve."""

from squid.core.errors import NotFoundError
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
