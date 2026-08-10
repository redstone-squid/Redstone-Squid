"""Account context errors."""

from uuid import UUID

from squid.core.errors import ConflictError, ErrorCode, NotFoundError, ServiceUnavailableError, ValidationError
from squid.core.i18n import _


class InvalidAccountError(ValidationError):
    """Account data is invalid."""

    default_message = _("The account data is invalid.")
    default_code = ErrorCode.INVALID_ACCOUNT
    default_resource = "account"


class AccountNotFoundError(NotFoundError):
    """An application account could not be found."""

    default_message = _("Account not found.")
    default_code = ErrorCode.ACCOUNT_NOT_FOUND
    default_resource = "account"

    def __init__(self, account_id: int | None = None, *, discord_id: int | None = None) -> None:
        context = {"account_id": account_id} if account_id is not None else {"discord_id": discord_id}
        super().__init__(context=context)
        self.account_id = account_id
        self.discord_id = discord_id


class InvalidMergeProofError(ValidationError):
    """Both accounts were not authenticated recently enough for a merge."""

    default_message = _("Both accounts must be authenticated again before they can be merged.")
    default_title = _("Recent account proof required")
    default_resource = "account_merge"
    default_end_user_action = _("Authenticate both accounts again, then retry the merge.")


class InvalidVerificationCodeError(ValidationError):
    """A verification code is invalid or expired."""

    default_message = _("The verification code is invalid or expired.")
    default_title = _("Invalid verification code")
    default_code = ErrorCode.INVALID_VERIFICATION_CODE
    default_resource = "verification_code"
    default_end_user_action = _("Generate a new code and try again.")


class AccountAlreadyLinkedError(ConflictError):
    """A Discord account is linked to a different Minecraft account."""

    default_message = _("This Discord account is already linked to a different Minecraft account.")
    default_title = _("Account already linked")
    default_code = ErrorCode.ACCOUNT_ALREADY_LINKED
    default_resource = "account"
    default_end_user_action = _("Unlink the current account before linking a new one.")

    def __init__(self, discord_id: int, minecraft_uuid: UUID) -> None:
        super().__init__(context={"discord_id": discord_id, "minecraft_uuid": str(minecraft_uuid)})
        self.discord_id = discord_id
        self.minecraft_uuid = minecraft_uuid


class ConsentRequiredError(ValidationError):
    """An account must accept the current privacy notice before this action."""

    default_message = _("You need to accept the current privacy notice before completing this action.")
    default_title = _("Consent required")
    default_code = ErrorCode.CONSENT_REQUIRED
    default_resource = "account"
    default_end_user_action = _("Review and accept the current privacy notice, then try again.")

    def __init__(self, discord_id: int) -> None:
        super().__init__(context={"discord_id": discord_id})
        self.discord_id = discord_id


class CreatorAliasNotFoundError(NotFoundError):
    """No build credits a creator under the requested name."""

    default_message = _("No build credits a creator by that name.")
    default_title = _("Creator name not found")
    default_code = ErrorCode.CREATOR_ALIAS_NOT_FOUND
    default_resource = "creator_alias"
    default_end_user_action = _("Check the spelling against the creator list on a build you worked on.")

    def __init__(self, name: str) -> None:
        super().__init__(context={"name": name}, public_context={"name": name})
        self.name = name


class AliasAlreadyClaimedError(ConflictError):
    """A creator name is already credited to an account."""

    default_message = _("That creator name is already claimed by another account.")
    default_title = _("Creator name already claimed")
    default_code = ErrorCode.ALIAS_ALREADY_CLAIMED
    default_resource = "creator_alias"
    default_end_user_action = _("Ask staff to review the claim if you believe the name is yours.")

    def __init__(self, name: str) -> None:
        super().__init__(context={"name": name}, public_context={"name": name})
        self.name = name


class ClaimNotFoundError(NotFoundError):
    """No pending claim matches the requested identifier."""

    default_message = _("No pending claim matches that ID.")
    default_title = _("Claim not found")
    default_code = ErrorCode.CLAIM_NOT_FOUND
    default_resource = "creator_alias_claim"

    def __init__(self, claim_id: int) -> None:
        super().__init__(context={"claim_id": claim_id}, public_context={"claim_id": claim_id})
        self.claim_id = claim_id


class MinecraftAccountNotFoundError(NotFoundError):
    """A Minecraft UUID does not identify an account."""

    default_message = _("Minecraft account not found.")
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "minecraft_account"
    default_end_user_action = _("Check the UUID and try again.")

    def __init__(self, minecraft_uuid: UUID) -> None:
        value = str(minecraft_uuid)
        super().__init__(
            context={"minecraft_uuid": value},
            public_context={"minecraft_uuid": value},
        )
        self.minecraft_uuid = minecraft_uuid


class MinecraftServiceUnavailableError(ServiceUnavailableError):
    """The Mojang session service failed."""

    default_message = _("The Minecraft account service is temporarily unavailable.")
    default_code = ErrorCode.MINECRAFT_SERVICE_UNAVAILABLE
    default_resource = "minecraft_account"
    default_end_user_action = _("Try again in a few minutes.")
