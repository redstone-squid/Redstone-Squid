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


class RecordDefinitionNotFoundError(NotFoundError):
    """No record definition exists with the requested identifier."""

    default_message = _("Record category not found.")
    default_title = _("Record category not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record_definition"

    def __init__(self, definition_id: int) -> None:
        super().__init__(
            context={"definition_id": definition_id},
            public_context={"definition_id": definition_id},
        )
        self.definition_id = definition_id


class NoMatchingRecordCategoryError(NotFoundError):
    """No confirmed build satisfies the requested record category."""

    default_message = _("No confirmed build satisfies the requested record category.")
    default_title = _("Record category not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record_category"

    def __init__(self, *, kind: str, base_key: str) -> None:
        # The base key is an internal identity string; logs keep it, API payloads do not.
        super().__init__(
            context={"kind": kind, "base_key": base_key},
            public_context={"kind": kind},
        )
