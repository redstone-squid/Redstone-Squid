"""Tracked message context errors."""

from squid.core.errors import ErrorCode, NotFoundError, ValidationError


class InvalidMessageError(ValidationError):
    """Tracked message metadata is invalid."""

    default_message = "The message metadata is invalid."
    default_code = ErrorCode.INVALID_MESSAGE
    default_resource = "message"


class MessageNotFoundError(NotFoundError):
    """A tracked message could not be found."""

    default_message = "Tracked message not found."
    default_code = ErrorCode.MESSAGE_NOT_FOUND
    default_resource = "message"

    def __init__(self, message_id: int) -> None:
        super().__init__(
            context={"message_id": message_id},
            public_context={"message_id": message_id},
        )
        self.message_id = message_id
