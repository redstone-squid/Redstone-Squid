"""Tag context errors."""

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import tr


class TagNotFoundError(NotFoundError):
    """No tag definition exists for the requested identifier."""

    default_message = tr(t"tags-errors-tag-not-found = Tag not found.")
    default_title = tr(t"tags-tag-not-found-title = Tag not found")
    default_code = ErrorCode.TAG_NOT_FOUND
    default_resource = "tag"

    def __init__(self, tag_id: int | str) -> None:
        context = {"slug" if isinstance(tag_id, str) else "tag_id": tag_id}
        super().__init__(context=context, public_context=context)
        self.tag_id = tag_id
