"""User account application services."""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from squid.users.application.ports import UserRepository
from squid.users.domain import AliasClaim, ClaimMethod, ClaimStatus, CreatorAlias, UserAccount, UserConsent
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

    async def link_minecraft_account(self, discord_id: int, code: str, *, consent: UserConsent) -> CreatorAlias | None:
        """Link a Discord user with consent using a valid, unexpired verification code.

        Returns the creator alias claimed automatically because it matched the
        verified username, or ``None`` if no unclaimed alias matched.
        """
        verification_code = await self._repository.get_valid_verification_code(code)
        if verification_code is None:
            raise InvalidVerificationCodeError

        user = await self._repository.get_by_discord_id(discord_id)
        if user is None:
            user = await self._repository.add(
                consent=consent,
                discord_id=discord_id,
                minecraft_uuid=verification_code.minecraft_uuid,
                ign=verification_code.username,
            )
        else:
            if user.minecraft_uuid is not None and user.minecraft_uuid != verification_code.minecraft_uuid:
                raise AccountAlreadyLinkedError(discord_id, user.minecraft_uuid)

            user.minecraft_uuid = verification_code.minecraft_uuid
            user.ign = verification_code.username
            user.consent = consent
            await self._repository.update(user)

        if user.id is None:
            return None
        return await self._repository.claim_unclaimed_alias(
            user_id=user.id,
            name=verification_code.username,
            method=ClaimMethod.VERIFIED_IGN,
        )

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

        await self._repository.invalidate_codes(minecraft_uuid)
        code = self._verification_code_factory()
        await self._repository.create_verification_code(
            minecraft_uuid=minecraft_uuid, code=str(code), username=minecraft_username
        )
        return code
