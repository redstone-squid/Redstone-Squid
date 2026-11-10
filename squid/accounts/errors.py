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
from squid.core.i18n import tr
from squid_i18n import Message


class InvalidAccountError(ValidationError):
    """Account data is invalid."""

    default_message = tr(t"accounts-account-data-invalid = The account data is invalid.")
    default_code = ErrorCode.INVALID_ACCOUNT
    default_resource = "account"


def _identity_context(
    account_id: int | None,
    provider: IdentityProvider | None,
    subject: str | None,
) -> dict[str, JSONValue]:
    """Return available account and provider identifiers for internal error context."""
    context: dict[str, JSONValue] = {}
    if account_id is not None:
        context["account_id"] = account_id
    if provider is not None and subject is not None:
        context["provider"] = provider
        context["subject"] = subject
    return context


class AccountNotFoundError(NotFoundError):
    """An application account could not be found."""

    default_message = tr(t"accounts-errors-account-not-found = Account not found.")
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


class AccountIdentityNotFoundError(NotFoundError):
    """The account has no identity with the requested internal id.

    Identities are addressed by id rather than by provider because an account can legitimately
    hold two of the same provider — a merge moves every identity row across.
    """

    default_message = tr(t"accounts-linked-identity-not-owned = That linked identity does not belong to your account.")
    default_title = tr(t"accounts-errors-identity-not-found = Identity not found")
    default_code = ErrorCode.ACCOUNT_IDENTITY_NOT_FOUND
    default_resource = "account_identity"
    default_end_user_action = tr(
        t"accounts-list-linked-identities = List your linked identities and try again with one of those."
    )

    def __init__(self, identity_id: int, *, account_id: int | None = None) -> None:
        context: dict[str, JSONValue] = {"identity_id": identity_id}
        if account_id is not None:
            context["account_id"] = account_id
        super().__init__(context=context, public_context={"identity_id": identity_id})
        self.identity_id = identity_id
        self.account_id = account_id


class LastIdentityError(ConflictError):
    """Unlinking would leave the account without a sign-in identity."""

    default_message = tr(t"accounts-last-identity-required = You cannot unlink your only remaining identity.")
    default_title = tr(t"accounts-errors-last-identity = Last identity")
    default_code = ErrorCode.LAST_IDENTITY
    default_resource = "account_identity"
    default_end_user_action = tr(
        t"accounts-link-identity-before-unlink = Link another identity first, then unlink this one."
    )

    def __init__(self, *, account_id: int | None = None) -> None:
        super().__init__(context={} if account_id is None else {"account_id": account_id})
        self.account_id = account_id


class InvalidProfileError(ValidationError):
    """Profile content failed validation."""

    default_message = tr(t"accounts-profile-data-invalid = The profile data is invalid.")
    default_title = tr(t"accounts-errors-invalid-profile = Invalid profile")
    default_code = ErrorCode.INVALID_PROFILE
    default_resource = "account_profile"


class InvalidMergeCodeError(ValidationError):
    """A merge code is unknown, already spent, or expired."""

    default_message = tr(t"accounts-merge-code-invalid = That merge code is invalid or expired.")
    default_title = tr(t"accounts-errors-invalid-merge-code = Invalid merge code")
    default_code = ErrorCode.INVALID_MERGE_CODE
    default_resource = "account_merge"
    default_end_user_action = tr(
        t"accounts-errors-generate-a-new-merge-code-from-the = Generate a new merge code from the account "
        t"you want to absorb, then try again."
    )


class InvalidMergeProofError(ValidationError):
    """The merge names the same account twice or lacks recent proof for both accounts."""

    default_message = tr(
        t"accounts-merge-proof-required = Both accounts must be authenticated again before they can be merged."
    )
    default_title = tr(t"accounts-recent-account-proof-required = Recent account proof required")
    default_resource = "account_merge"
    default_end_user_action = tr(
        t"accounts-merge-reauthenticate = Authenticate both accounts again, then retry the merge."
    )


class InvalidVerificationCodeError(ValidationError):
    """A verification code is invalid or expired."""

    default_message = tr(t"accounts-verification-code-invalid = The verification code is invalid or expired.")
    default_title = tr(t"accounts-errors-invalid-verification-code = Invalid verification code")
    default_code = ErrorCode.INVALID_VERIFICATION_CODE
    default_resource = "verification_code"
    default_end_user_action = tr(
        t"accounts-errors-generate-a-new-code-and-try-again = Generate a new code and try again."
    )


class LinkReservationExpiredError(ValidationError):
    """The reservation token no longer matches a live hold on the verification code."""

    default_message = tr(t"accounts-link-prompt-expired = The linking prompt expired before you answered it.")
    default_title = tr(t"accounts-errors-prompt-expired = Prompt expired")
    default_code = ErrorCode.LINK_RESERVATION_EXPIRED
    default_resource = "verification_code"
    default_end_user_action = tr(t"accounts-link-restart = Run /account link again with a fresh code from the game.")


class VerificationAttemptsExhaustedError(RateLimitedError):
    """An external identity has reached the wrong-code limit.

    The API exposes this as a rate-limit response with Retry-After. The restriction
    limits guessing at codes that can link someone else's Minecraft identity.
    """

    default_message = tr(t"accounts-verification-attempts-exceeded = Too many incorrect verification codes.")
    default_title = tr(t"accounts-errors-too-many-attempts = Too many attempts")
    default_code = ErrorCode.VERIFICATION_ATTEMPTS_EXHAUSTED
    default_resource = "verification_code"
    default_end_user_action = tr(
        t"accounts-errors-wait-for-the-cooling-off-period-to = Wait for the cooling-off period to pass, "
        t"then generate a new code in game."
    )


class AccountAlreadyLinkedError(ConflictError):
    """An account is already linked to a different Minecraft account."""

    default_message = tr(
        t"accounts-account-already-linked = Your account is already linked to a different Minecraft account."
    )
    default_title = tr(t"accounts-errors-account-already-linked = Account already linked")
    default_code = ErrorCode.ACCOUNT_ALREADY_LINKED
    default_resource = "account"
    default_end_user_action = tr(t"accounts-unlink-before-link = Unlink the current account before linking a new one.")

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

    default_message = tr(
        t"accounts-privacy-consent-required = You need to accept the current privacy "
        t"notice before completing this action."
    )
    default_title = tr(t"accounts-errors-consent-required = Consent required")
    default_code = ErrorCode.CONSENT_REQUIRED
    default_resource = "account"
    default_end_user_action = tr(
        t"accounts-privacy-notice-consent = Review and accept the current privacy notice, then try again."
    )

    def __init__(
        self,
        *,
        account_id: int | None = None,
        provider: IdentityProvider | None = None,
        subject: str | None = None,
    ) -> None:
        super().__init__(context=_identity_context(account_id, provider, subject))
        self.account_id = account_id


class StaleConsentNoticeError(ConflictError):
    """A client offered acceptance of a notice version that is no longer published.

    Recorded consent is only meaningful if the text the user read is the text the receipt names.
    A client holding a cached notice would otherwise record agreement to wording nobody saw.
    """

    default_message = tr(t"accounts-privacy-notice-updated = The privacy notice has changed since this one was shown.")
    default_title = tr(t"accounts-privacy-notice-outdated = Privacy notice out of date")
    default_code = ErrorCode.CONSENT_VERSION_STALE
    default_resource = "account"
    default_end_user_action = tr(
        t"accounts-privacy-notice-refresh = Re-read the current privacy notice, then accept it again."
    )

    def __init__(self, *, offered: str, current: str) -> None:
        super().__init__(
            context={"offered": offered, "current": current},
            public_context={"offered": offered, "current": current},
        )
        self.offered = offered
        self.current = current


class CreatorAliasNotFoundError(NotFoundError):
    """No creator alias exists with the requested normalized name."""

    default_message = tr(
        t"accounts-errors-no-build-credits-a-creator-by-that = No build credits a creator by that name."
    )
    default_title = tr(t"accounts-creator-name-not-found = Creator name not found")
    default_code = ErrorCode.CREATOR_ALIAS_NOT_FOUND
    default_resource = "creator_alias"
    default_end_user_action = tr(
        t"accounts-creator-name-check = Check the spelling against the creator list on a build you worked on."
    )

    def __init__(self, name: str) -> None:
        super().__init__(context={"name": name}, public_context={"name": name})
        self.name = name


class CreatorNotFoundError(NotFoundError):
    """No creator profile exists for the requested identifier."""

    default_message = tr(t"accounts-errors-creator-not-found = Creator not found.")
    default_title = tr(t"accounts-creator-not-found-title = Creator not found")
    default_code = ErrorCode.CREATOR_NOT_FOUND
    default_resource = "creator"
    default_end_user_action = tr(
        t"accounts-errors-check-the-creator-id-and-try-again = Check the creator ID and try again."
    )

    def __init__(self, creator_id: UUID) -> None:
        identifier = str(creator_id)
        super().__init__(context={"creator_id": identifier}, public_context={"creator_id": identifier})
        self.creator_id = creator_id


class AliasAlreadyClaimedError(ConflictError):
    """A creator name is already credited to an account.

    Public context may identify the holder by public creator ID and display name.
    The internal account ID remains in diagnostic context.
    """

    default_message = tr(t"accounts-creator-name-claimed = That creator name is already claimed by another account.")
    default_title = tr(t"accounts-creator-name-already-claimed = Creator name already claimed")
    default_code = ErrorCode.ALIAS_ALREADY_CLAIMED
    default_resource = "creator_alias"
    default_end_user_action = tr(
        t"accounts-errors-ask-staff-to-review-the-claim-if = Ask staff to review the claim if you believe "
        t"the name is yours."
    )

    def __init__(
        self,
        name: str,
        *,
        holder_public_creator_id: UUID | None = None,
        holder_account_id: int | None = None,
        end_user_action: str | Message | None = None,
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
        name = self.name
        return self.with_context(
            public_context={"holder_name": holder_name},
            message=tr(
                t"accounts-creator-already-credited = **{name}** is already credited "
                t"to the creator known as **{holder_name}**."
            ),
        )


class ClaimNotFoundError(NotFoundError):
    """No pending claim matches the requested identifier."""

    default_message = tr(t"accounts-no-pending-claim-matches-id = No pending claim matches that ID.")
    default_title = tr(t"accounts-errors-claim-not-found = Claim not found")
    default_code = ErrorCode.CLAIM_NOT_FOUND
    default_resource = "creator_alias_claim"

    def __init__(self, claim_id: int) -> None:
        super().__init__(context={"claim_id": claim_id}, public_context={"claim_id": claim_id})
        self.claim_id = claim_id


class NoLinkedMinecraftAccountError(NotFoundError):
    """The account has no Java identity to refresh."""

    default_message = tr(t"accounts-minecraft-account-required = You do not have a Minecraft account linked.")
    default_title = tr(t"accounts-no-minecraft-account-linked = No Minecraft account linked")
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "account_identity"
    default_end_user_action = tr(t"accounts-link-minecraft-first = Link a Minecraft account first, then try again.")

    def __init__(self, *, account_id: int | None = None) -> None:
        super().__init__(context={} if account_id is None else {"account_id": account_id})
        self.account_id = account_id


class MinecraftAccountNotFoundError(NotFoundError):
    """A Minecraft UUID does not identify an account."""

    default_message = tr(t"accounts-minecraft-account-not-found = Minecraft account not found.")
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "minecraft_account"
    default_end_user_action = tr(t"accounts-check-uuid-try-again = Check the UUID and try again.")

    def __init__(self, minecraft_uuid: UUID) -> None:
        value = str(minecraft_uuid)
        super().__init__(
            context={"minecraft_uuid": value},
            public_context={"minecraft_uuid": value},
        )
        self.minecraft_uuid = minecraft_uuid


class MinecraftServiceUnavailableError(ServiceUnavailableError):
    """The Mojang session service failed."""

    default_message = tr(
        t"accounts-minecraft-account-service = The Minecraft account service is temporarily unavailable."
    )
    default_code = ErrorCode.MINECRAFT_SERVICE_UNAVAILABLE
    default_resource = "minecraft_account"
    default_end_user_action = tr(t"accounts-retry-soon = Try again in a few minutes.")
