"""Discord message context errors."""

from squid.core.errors import ErrorCode, NotFoundError
from squid.core.i18n import _


class MessageNotFoundError(NotFoundError):
    """A recorded Discord message could not be found."""

    default_message = _("Tracked message not found.")
    default_code = ErrorCode.MESSAGE_NOT_FOUND
    default_resource = "message"

    def __init__(self, message_id: int) -> None:
        super().__init__(
            context={"message_id": message_id},
            public_context={"message_id": message_id},
        )
        self.message_id = message_id
