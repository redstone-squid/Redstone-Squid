"""Bot-owned Discord post context errors."""

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import tr


class PostNotFoundError(NotFoundError):
    """No bot-owned post is recorded for this Discord message."""

    default_message = tr(t"Tracked message not found.")
    default_code = ErrorCode.MESSAGE_NOT_FOUND
    default_resource = "post"

    def __init__(self, message_id: int) -> None:
        super().__init__(
            context={"message_id": message_id},
            public_context={"message_id": message_id},
        )
        self.message_id = message_id
