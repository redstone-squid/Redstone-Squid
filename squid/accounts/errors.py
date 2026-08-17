"""Account context errors."""

from uuid import UUID

from squid.accounts.domain import IdentityProvider
from squid.core.errors import (
    ConflictError,
    ErrorCode,
    JSONValue,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.i18n import _


class InvalidAccountError(ValidationError):
    """Account data is invalid."""

    default_message = _("The account data is invalid.")
    default_code = ErrorCode.INVALID_ACCOUNT
    default_resource = "account"


def _identity_context(
    account_id: int | None,
    provider: IdentityProvider | None,
    subject: str | None,
) -> dict[str, JSONValue]:
    """Describe whichever identity the caller was known by.

    Every namespace is named explicitly, so an error raised for a CLI device or a
    Minecraft player says which one it means rather than implying Discord by omission.
    """
    context: dict[str, JSONValue] = {}
    if account_id is not None:
        context["account_id"] = account_id
    if provider is not None and subject is not None:
        context["provider"] = provider
        context["subject"] = subject
    return context


class AccountNotFoundError(NotFoundError):
    """An application account could not be found."""

    default_message = _("Account not found.")
    default_code = ErrorCode.ACCOUNT_NOT_FOUND
    default_resource = "account"

    def __init__(
        self,
        account_id: int | None = None,
        *,
        provider: IdentityProvider | None = None,
        subject: str | None = None,
    ) -> None:
        super().__init__(context=_identity_context(account_id, provider, subject))
        self.account_id = account_id
        self.provider = provider
        self.subject = subject


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


class LinkReservationExpiredError(ValidationError):
    """A held verification code lapsed before its consent prompt was answered.

    Distinct from `InvalidVerificationCodeError` on purpose: the code was correct, and telling
    someone their good code was invalid sends them to fetch a new one for the wrong reason.
    """

    default_message = _("The linking prompt expired before you answered it.")
    default_title = _("Prompt expired")
    default_code = ErrorCode.LINK_RESERVATION_EXPIRED
    default_resource = "verification_code"
    default_end_user_action = _("Run /account link again with a fresh code from the game.")


class VerificationAttemptsExhaustedError(RateLimitedError):
    """Too many consecutive verification codes were refused for one identity.

    A `RateLimitedError`, so the API answers 429 with `Retry-After` and the bot renders the wait
    without any transport-specific handling. The lockout exists because a correct guess links
    somebody else's Minecraft account to the guesser, which is identity takeover rather than a
    nuisance: the code is the whole binding, and the redemption never mentions the UUID it was
    issued for.
    """

    default_message = _("Too many incorrect verification codes.")
    default_title = _("Too many attempts")
    default_code = ErrorCode.VERIFICATION_ATTEMPTS_EXHAUSTED
    default_resource = "verification_code"
    default_end_user_action = _("Wait for the cooling-off period to pass, then generate a new code in game.")


class AccountAlreadyLinkedError(ConflictError):
    """An account is already linked to a different Minecraft account."""

    default_message = _("Your account is already linked to a different Minecraft account.")
    default_title = _("Account already linked")
    default_code = ErrorCode.ACCOUNT_ALREADY_LINKED
    default_resource = "account"
    default_end_user_action = _("Unlink the current account before linking a new one.")

    def __init__(
        self,
        *,
        minecraft_uuid: UUID,
        account_id: int | None = None,
        provider: IdentityProvider | None = None,
        subject: str | None = None,
    ) -> None:
        context = _identity_context(account_id, provider, subject)
        context["minecraft_uuid"] = str(minecraft_uuid)
        super().__init__(context=context)
        self.account_id = account_id
        self.minecraft_uuid = minecraft_uuid


class ConsentRequiredError(ValidationError):
    """An account must accept the current privacy notice before this action."""

    default_message = _("You need to accept the current privacy notice before completing this action.")
    default_title = _("Consent required")
    default_code = ErrorCode.CONSENT_REQUIRED
    default_resource = "account"
    default_end_user_action = _("Review and accept the current privacy notice, then try again.")

    def __init__(
        self,
        *,
        account_id: int | None = None,
        provider: IdentityProvider | None = None,
        subject: str | None = None,
    ) -> None:
        super().__init__(context=_identity_context(account_id, provider, subject))
        self.account_id = account_id


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


class CreatorNotFoundError(NotFoundError):
    """No creator profile exists for the requested identifier."""

    default_message = _("Creator not found.")
    default_title = _("Creator not found")
    default_code = ErrorCode.CREATOR_NOT_FOUND
    default_resource = "creator"
    default_end_user_action = _("Check the creator ID and try again.")

    def __init__(self, creator_id: UUID) -> None:
        identifier = str(creator_id)
        super().__init__(context={"creator_id": identifier}, public_context={"creator_id": identifier})
        self.creator_id = creator_id


class AliasAlreadyClaimedError(ConflictError):
    """A creator name is already credited to an account.

    Carries *which* creator holds it, not just that somebody does. A creator profile is public data —
    `GET /v1/creators/{creator_id}` serves it unauthenticated — so naming the holder discloses
    nothing that was private, and without it the error tells the affected user nothing they can act
    on. The internal account ID stays in `context`, which is log-only.
    """

    default_message = _("That creator name is already claimed by another account.")
    default_title = _("Creator name already claimed")
    default_code = ErrorCode.ALIAS_ALREADY_CLAIMED
    default_resource = "creator_alias"
    default_end_user_action = _("Ask staff to review the claim if you believe the name is yours.")

    def __init__(
        self,
        name: str,
        *,
        holder_public_creator_id: UUID | None = None,
        holder_account_id: int | None = None,
        end_user_action: str | None = None,
    ) -> None:
        public_context: dict[str, JSONValue] = {"name": name}
        if holder_public_creator_id is not None:
            public_context["public_creator_id"] = str(holder_public_creator_id)
        context: dict[str, JSONValue] = dict(public_context)
        if holder_account_id is not None:
            context["holder_account_id"] = holder_account_id
        super().__init__(context=context, public_context=public_context, end_user_action=end_user_action)
        self.name = name
        self.holder_public_creator_id = holder_public_creator_id
        self.holder_account_id = holder_account_id

    def with_holder_name(self, holder_name: str) -> AliasAlreadyClaimedError:
        """Name the holder in the user-facing message once something has resolved it.

        Resolving a public creator profile costs a query, so it happens in the service on the error
        path rather than in the repository on every raise.
        """
        return self.with_context(
            public_context={"holder_name": holder_name},
            message=_("**{name}** is already credited to the creator known as **{holder_name}**."),
            message_params={"name": self.name, "holder_name": holder_name},
        )


class ClaimNotFoundError(NotFoundError):
    """No pending claim matches the requested identifier."""

    default_message = _("No pending claim matches that ID.")
    default_title = _("Claim not found")
    default_code = ErrorCode.CLAIM_NOT_FOUND
    default_resource = "creator_alias_claim"

    def __init__(self, claim_id: int) -> None:
        super().__init__(context={"claim_id": claim_id}, public_context={"claim_id": claim_id})
        self.claim_id = claim_id


class NoLinkedMinecraftAccountError(NotFoundError):
    """The account has no Java identity to refresh."""

    default_message = _("You do not have a Minecraft account linked.")
    default_title = _("No Minecraft account linked")
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "account_identity"
    default_end_user_action = _("Link a Minecraft account first, then try again.")

    def __init__(self, *, account_id: int | None = None) -> None:
        super().__init__(context={} if account_id is None else {"account_id": account_id})
        self.account_id = account_id


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
