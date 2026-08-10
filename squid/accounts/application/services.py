"""Provider-neutral account application services."""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from whenever import Instant

from squid.accounts.application.ports import AccountRepository
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountMerge,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    RecentAccountProof,
)
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountNotFoundError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
)


class AccountService:
    """Orchestrate external identities, account merges, and creator credit claims."""

    def __init__(
        self,
        repository: AccountRepository,
        minecraft_username_lookup: Callable[[UUID], Awaitable[str | None]],
        verification_code_factory: Callable[[], int],
    ):
        self._repository = repository
        self._minecraft_username_lookup = minecraft_username_lookup
        self._verification_code_factory = verification_code_factory

    async def get_account(self, discord_id: int) -> Account | None:
        """Return the account holding *discord_id*, or ``None``."""
        return await self._repository.get_by_discord_id(discord_id)

    async def get_account_by_id(self, account_id: int) -> Account | None:
        """Return an account by its internal identifier."""
        return await self._repository.get_by_id(account_id)

    async def get_or_create_account(self, discord_id: int) -> Account:
        """Resolve the account established by a Discord OAuth or gateway identity."""
        return await self._repository.get_or_create_discord(discord_id)

    async def grant_current_consent(self, discord_id: int) -> Account:
        """Record acceptance of the current privacy notice."""
        account = await self._repository.get_by_discord_id(discord_id)
        if account is None or account.id is None:
            raise AccountNotFoundError(discord_id=discord_id)
        return await self._repository.update_consent(account.id, AccountConsent.grant_current())

    async def merge_accounts(
        self,
        surviving_proof: RecentAccountProof,
        absorbed_proof: RecentAccountProof,
        *,
        now: Instant | None = None,
    ) -> AccountMerge:
        """Merge two accounts only after recent, independent proof of both."""
        checked_at = now or Instant.now()
        if (
            surviving_proof.account_id == absorbed_proof.account_id
            or not surviving_proof.is_recent_at(checked_at)
            or not absorbed_proof.is_recent_at(checked_at)
        ):
            raise InvalidMergeProofError
        return await self._repository.merge(surviving_proof.account_id, absorbed_proof.account_id)

    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        """Return a creator credit by name without loading its linked account."""
        return await self._repository.get_alias_by_name(name)

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        """Return a public creator profile, following permanent merge redirects."""
        return await self._repository.get_creator_profile(public_id)

    async def link_minecraft_account(
        self, discord_id: int, code: str, *, consent: AccountConsent
    ) -> CreatorAlias | None:
        """Attach a verified Java identity using a valid one-time code."""
        result = await self._repository.consume_code_and_link_account(
            discord_id=discord_id,
            code=code,
            consent=consent,
        )
        if result.account is None:
            raise InvalidVerificationCodeError
        if result.conflicting_java_uuid is not None:
            raise AccountAlreadyLinkedError(discord_id, result.conflicting_java_uuid)
        return result.claimed_alias

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink every Java identity from the Discord-linked account."""
        return await self._repository.unlink_java_identity(discord_id)

    async def request_alias_claim(self, discord_id: int, name: str) -> AliasClaim:
        """Open a staff-reviewed request to be credited under a creator name."""
        account = await self._repository.get_by_discord_id(discord_id)
        if account is None or account.id is None:
            raise AccountNotFoundError(discord_id=discord_id)
        if account.needs_consent_refresh:
            raise ConsentRequiredError(discord_id)
        return await self._repository.request_claim(name=name, account_id=account.id)

    async def pending_alias_claims(self) -> Sequence[AliasClaim]:
        """List creator credit claims awaiting staff review."""
        return await self._repository.pending_claims()

    async def approve_alias_claim(self, claim_id: int, *, staff_account_id: int) -> AliasClaim:
        """Credit the claimant with the alias and close the request."""
        return await self._resolve_claim(claim_id, ClaimStatus.APPROVED, staff_account_id)

    async def reject_alias_claim(self, claim_id: int, *, staff_account_id: int) -> AliasClaim:
        """Close the request without crediting the claimant."""
        return await self._resolve_claim(claim_id, ClaimStatus.REJECTED, staff_account_id)

    async def _resolve_claim(self, claim_id: int, status: ClaimStatus, staff_account_id: int) -> AliasClaim:
        claim = await self._repository.get_claim(claim_id)
        if claim is None or claim.status is not ClaimStatus.PENDING:
            raise ClaimNotFoundError(claim_id)
        return await self._repository.resolve_claim(
            claim_id=claim_id,
            status=status,
            resolved_by_account_id=staff_account_id,
        )

    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
        """Generate a verification code after validating the Java account."""
        minecraft_username = await self._minecraft_username_lookup(minecraft_uuid)
        if minecraft_username is None:
            raise MinecraftAccountNotFoundError(minecraft_uuid)
        code = self._verification_code_factory()
        await self._repository.replace_verification_code(
            minecraft_uuid=minecraft_uuid,
            code=str(code),
            username=minecraft_username,
        )
        return code
