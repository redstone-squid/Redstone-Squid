"""Record context errors."""

from uuid import UUID

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import tr


class RecordNotFoundError(NotFoundError):
    """No published record or record range exists for the requested identifier."""

    default_message = tr(t"records-errors-record-not-found = Record not found.")
    default_title = tr(t"records-record-not-found-title = Record not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record"

    def __init__(self, record_id: UUID | int) -> None:
        identifier = str(record_id) if isinstance(record_id, UUID) else record_id
        super().__init__(context={"record_id": identifier}, public_context={"record_id": identifier})
        self.record_id = record_id


class RecordDefinitionNotFoundError(NotFoundError):
    """No record definition exists with the requested identifier."""

    default_message = tr(t"records-record-category-not-found = Record category not found.")
    default_title = tr(t"records-category-not-found-title = Record category not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record_definition"

    def __init__(self, definition_id: int) -> None:
        super().__init__(
            context={"definition_id": definition_id},
            public_context={"definition_id": definition_id},
        )
        self.definition_id = definition_id


class NoMatchingRecordCategoryError(NotFoundError):
    """No approved build satisfies the requested record category."""

    default_message = tr(t"records-no-confirmed-build = No approved build satisfies the requested record category.")
    default_title = tr(t"records-category-not-found-title = Record category not found")
    default_code = ErrorCode.RECORD_NOT_FOUND
    default_resource = "record_category"

    def __init__(self, *, kind: str, base_key: str) -> None:
        # The base key is an internal identity string; logs keep it, API payloads do not.
        super().__init__(
            context={"kind": kind, "base_key": base_key},
            public_context={"kind": kind},
        )
