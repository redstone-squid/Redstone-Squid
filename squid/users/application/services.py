"""User account application services."""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from squid.users.application.ports import UserRepository
from squid.users.domain import AliasClaim, ClaimStatus, CreatorAlias, CreatorProfile, UserAccount, UserConsent
from squid.users.errors import (
    AccountAlreadyLinkedError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
    UserNotFoundError,
)


class UserService:
    """Orchestrate user creation, Minecraft verification, and creator credit claims."""

    def __init__(
        self,
        repository: UserRepository,
        minecraft_username_lookup: Callable[[UUID], Awaitable[str | None]],
        verification_code_factory: Callable[[], int],
    ):
        self._repository = repository
        self._minecraft_username_lookup = minecraft_username_lookup
        self._verification_code_factory = verification_code_factory

    async def get_account(self, discord_id: int) -> UserAccount | None:
        """Return the account for *discord_id*, or ``None`` if there is none."""
        return await self._repository.get_by_discord_id(discord_id)

    async def get_or_create_account(self, discord_id: int) -> UserAccount:
        """Resolve the minimal account identity established by OAuth login."""
        return await self._repository.get_or_create_discord(discord_id)

    async def grant_current_consent(self, discord_id: int) -> UserAccount:
        """Record acceptance of the current privacy notice for an existing account."""
        user = await self._repository.get_by_discord_id(discord_id)
        if user is None:
            raise UserNotFoundError(discord_id)
        user.consent = UserConsent.grant_current()
        await self._repository.update(user)
        return user

    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        """Return a creator credit by name without loading its linked account."""
        return await self._repository.get_alias_by_name(name)

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        """Return a public creator profile and all aliases claimed by it."""
        return await self._repository.get_creator_profile(public_id)

    async def link_minecraft_account(self, discord_id: int, code: str, *, consent: UserConsent) -> CreatorAlias | None:
        """Link a Discord user with consent using a valid, unexpired verification code.

        Returns the creator alias claimed automatically because it matched the
        verified username, or ``None`` if no unclaimed alias matched.
        """
        result = await self._repository.consume_code_and_link_account(
            discord_id=discord_id,
            code=code,
            consent=consent,
        )
        if result.account is None:
            raise InvalidVerificationCodeError
        if result.conflicting_minecraft_uuid is not None:
            raise AccountAlreadyLinkedError(discord_id, result.conflicting_minecraft_uuid)
        return result.claimed_alias

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink a user's Minecraft account."""
        return await self._repository.unlink_minecraft_account(discord_id)

    async def request_alias_claim(self, discord_id: int, name: str) -> AliasClaim:
        """Open a staff-reviewed request to be credited under a creator name.

        Used for names a creator no longer holds on Mojang, which the automatic
        claim during linking cannot match.
        """
        user = await self._repository.get_by_discord_id(discord_id)
        if user is None or user.id is None:
            raise UserNotFoundError(discord_id)
        if user.needs_consent_refresh:
            # Approving the claim would attach build attribution to this
            # account, which the current notice has to cover first.
            raise ConsentRequiredError(discord_id)
        return await self._repository.request_claim(name=name, user_id=user.id)

    async def pending_alias_claims(self) -> Sequence[AliasClaim]:
        """List creator credit claims awaiting staff review."""
        return await self._repository.pending_claims()

    async def approve_alias_claim(self, claim_id: int, *, staff_discord_id: int) -> AliasClaim:
        """Credit the claimant with the alias and close the request."""
        return await self._resolve_claim(claim_id, ClaimStatus.APPROVED, staff_discord_id)

    async def reject_alias_claim(self, claim_id: int, *, staff_discord_id: int) -> AliasClaim:
        """Close the request without crediting the claimant."""
        return await self._resolve_claim(claim_id, ClaimStatus.REJECTED, staff_discord_id)

    async def _resolve_claim(self, claim_id: int, status: ClaimStatus, staff_discord_id: int) -> AliasClaim:
        claim = await self._repository.get_claim(claim_id)
        if claim is None or claim.status is not ClaimStatus.PENDING:
            raise ClaimNotFoundError(claim_id)
        return await self._repository.resolve_claim(
            claim_id=claim_id, status=status, resolved_by_discord_id=staff_discord_id
        )

    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
        """Generate a verification code after validating the Minecraft account."""
        minecraft_username = await self._minecraft_username_lookup(minecraft_uuid)
        if minecraft_username is None:
            raise MinecraftAccountNotFoundError(minecraft_uuid)

        code = self._verification_code_factory()
        await self._repository.replace_verification_code(
            minecraft_uuid=minecraft_uuid, code=str(code), username=minecraft_username
        )
        return code
