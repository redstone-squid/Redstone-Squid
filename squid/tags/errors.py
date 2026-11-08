"""Tag context errors."""

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import tr


class TagNotFoundError(NotFoundError):
    """No tag definition exists for the requested identifier."""

    default_message = tr("Tag not found.")
    default_title = tr("Tag not found")
    default_code = ErrorCode.TAG_NOT_FOUND
    default_resource = "tag"

    def __init__(self, tag_id: int) -> None:
        super().__init__(context={"tag_id": tag_id}, public_context={"tag_id": tag_id})
        self.tag_id = tag_id
