"""Record context errors."""

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import _


class RecordNotFoundError(NotFoundError):
    """No published result exists for the requested identifier.

    A result whose computation run is no longer the published one is not found
    either: records are addressed by result id, and a superseded run's results
    are not part of the catalogue.
    """

    default_message = _("Record not found.")
    default_title = _("Record not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record"

    def __init__(self, record_id: int) -> None:
        super().__init__(context={"record_id": record_id}, public_context={"record_id": record_id})
        self.record_id = record_id
