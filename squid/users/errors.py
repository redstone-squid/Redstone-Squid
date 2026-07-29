"""User context errors."""

from uuid import UUID

from squid.core.errors import ConflictError, ErrorCode, NotFoundError, ServiceUnavailableError, ValidationError


class InvalidUserError(ValidationError):
    """User data is invalid."""

    default_message = "The user data is invalid."
    default_code = ErrorCode.INVALID_USER
    default_resource = "user"


class UserNotFoundError(NotFoundError):
    """An application user could not be found."""

    default_message = "User not found."
    default_code = ErrorCode.USER_NOT_FOUND
    default_resource = "user"

    def __init__(self, discord_id: int) -> None:
        super().__init__(context={"discord_id": discord_id})
        self.discord_id = discord_id


class InvalidVerificationCodeError(ValidationError):
    """A verification code is invalid or expired."""

    default_message = "The verification code is invalid or expired."
    default_title = "Invalid verification code"
    default_code = ErrorCode.INVALID_VERIFICATION_CODE
    default_resource = "verification_code"
    default_end_user_action = "Generate a new code and try again."


class AccountAlreadyLinkedError(ConflictError):
    """A Discord account is linked to a different Minecraft account."""

    default_message = "This Discord account is already linked to a different Minecraft account."
    default_title = "Account already linked"
    default_code = ErrorCode.ACCOUNT_ALREADY_LINKED
    default_resource = "user"
    default_end_user_action = "Unlink the current account before linking a new one."

    def __init__(self, discord_id: int, minecraft_uuid: UUID) -> None:
        super().__init__(context={"discord_id": discord_id, "minecraft_uuid": str(minecraft_uuid)})
        self.discord_id = discord_id
        self.minecraft_uuid = minecraft_uuid


class MinecraftAccountNotFoundError(NotFoundError):
    """A Minecraft UUID does not identify an account."""

    default_message = "Minecraft account not found."
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "minecraft_account"
    default_end_user_action = "Check the UUID and try again."

    def __init__(self, minecraft_uuid: UUID) -> None:
        value = str(minecraft_uuid)
        super().__init__(
            context={"minecraft_uuid": value},
            public_context={"minecraft_uuid": value},
        )
        self.minecraft_uuid = minecraft_uuid


class MinecraftServiceUnavailableError(ServiceUnavailableError):
    """The Mojang session service failed."""

    default_message = "The Minecraft account service is temporarily unavailable."
    default_code = ErrorCode.MINECRAFT_SERVICE_UNAVAILABLE
    default_resource = "minecraft_account"
    default_end_user_action = "Try again in a few minutes."
