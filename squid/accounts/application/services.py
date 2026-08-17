"""Account application services: one account may hold Discord and Java identities alike."""

import logging
import secrets
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
    IdentityProvider,
    IdentityRefresh,
    LinkReservation,
    RecentAccountProof,
)
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountNotFoundError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    LinkReservationExpiredError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
    VerificationAttemptsExhaustedError,
)
from squid.core.errors import InvalidStateError

logger = logging.getLogger(__name__)

VERIFICATION_MAX_CONSECUTIVE_FAILURES = 5
"""Wrong codes tolerated in a row before an identity has to wait.

Generous enough that mistyping a ten-digit code is never the problem, and small enough that the
guessing budget stays far below the code space even across many windows.
"""

VERIFICATION_LOCKOUT_SECONDS = 15 * 60
"""Longer than a code lives, so a lockout always outlasts the codes it was protecting."""

VERIFICATION_CODE_DIGITS = 10

LINK_RESERVATION_TTL_SECONDS = 180
"""How long a held code waits for its consent prompt to be answered.

Comfortably longer than the prompt's own 120-second timeout, so the view always expires first and
the user sees a disabled prompt rather than a hold that lapsed underneath a live one.
"""


def generate_verification_code() -> int:
    """Mint a ten-digit verification code, about 33 bits.

    Six digits was about 19.8 bits, and the redemption looks a code up by code alone across every
    outstanding code, so the chance per attempt was `outstanding / 900_000` rather than one in
    900 000 — and a hit links the victim's Minecraft account to the guesser.

    Stays numeric on purpose: `/verify` returns an `int` to the in-game plugin that shows the code
    to the player, so base32 would be stronger and would also change that response type. Thirty-three
    bits against a ten-minute window and a capped attempt budget is already decisive.
    """
    lower = 10 ** (VERIFICATION_CODE_DIGITS - 1)
    return secrets.randbelow(9 * lower) + lower


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

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        """Return the account holding one canonical provider subject, or ``None``."""
        return await self._repository.get_by_identity(provider, subject)

    async def get_account_by_id(self, account_id: int) -> Account | None:
        """Return an account by its internal identifier."""
        return await self._repository.get_by_id(account_id)

    async def get_accounts(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return several accounts, with their identities, at a fixed query cost.

        Anything rendering a list of account-keyed rows needs this rather than a
        `get_account_by_id` per row.
        """
        return await self._repository.get_many(account_ids)

    async def get_or_create_identity(self, provider: IdentityProvider, subject: str) -> Account:
        """Resolve the account established by a verified external identity, creating it if absent.

        Every caller holds evidence of the *provider* subject it passes — an OAuth exchange, a
        gateway event — which is what makes creating an account here legitimate. Nothing that
        merely observes a subject may use this; see `AccountIdCache`'s never-create rule.
        """
        return await self._repository.get_or_create_identity(provider, subject)

    async def grant_current_consent(self, account_id: int) -> Account:
        """Record consent for any authenticated caller, however they proved their identity."""
        return await self._repository.update_consent(account_id, AccountConsent.grant_current())

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

    async def guard_verification_attempts(self, attempted_by: tuple[IdentityProvider, str]) -> None:
        """Refuse a redemption while *attempted_by* is in its cooling-off period.

        Separate from the redemption itself so anything that tests a code — a redemption now, a
        reservation later — passes through one cap instead of each surface growing its own.
        """
        provider, subject = attempted_by
        locked_until = await self._repository.verification_lockout(provider, subject)
        if locked_until is not None:
            retry_after = max(1, round((locked_until - Instant.now()).total("seconds")))
            # The subject is deliberately not logged: for Discord it is a user ID, which
            # `_safe_log_context` strips everywhere else. The provider and the wait are what an
            # operator needs to tell abuse from a confused user.
            logger.warning(
                "verification.locked_out",
                extra={"provider": provider.value, "retry_after": retry_after},
            )
            raise VerificationAttemptsExhaustedError(retry_after)

    async def reserve_minecraft_link(
        self,
        code: str,
        *,
        attempted_by: tuple[IdentityProvider, str],
        ttl_seconds: int = LINK_RESERVATION_TTL_SECONDS,
    ) -> LinkReservation:
        """Hold a code so a consent prompt can say what agreeing to it will do.

        This is where a wrong code now fails — before the notice is read, rather than after it has
        been agreed to. It is also where the guessing happens, so it is capped like a redemption:
        without that, reserving would be an uncapped oracle sitting beside a capped one.
        """
        await self.guard_verification_attempts(attempted_by)
        reservation = await self._repository.reserve_verification_code(code, ttl_seconds=ttl_seconds)
        if reservation is None:
            await self._record_verification_failure(attempted_by)
            raise InvalidVerificationCodeError
        return reservation

    async def release_minecraft_link(self, code: str, reservation: LinkReservation) -> None:
        """Give a held code back after a declined or abandoned prompt.

        Best-effort by design: if this never runs, `reserved_until` lapses on its own, so a crash
        costs one prompt's delay rather than a permanently stuck code.
        """
        await self._repository.release_verification_code(code, reservation.token)

    async def link_minecraft_account(
        self,
        account_id: int,
        code: str,
        *,
        consent: AccountConsent,
        attempted_by: tuple[IdentityProvider, str],
        reservation: LinkReservation | None = None,
    ) -> IdentityRefresh:
        """Attach a verified Java identity to an existing account using a valid one-time code.

        *attempted_by* is the identity being rate-limited, which is not the same thing as
        *account_id*: the cap has to survive a caller who has no account yet, and it is keyed on the
        external identity doing the guessing.

        Passing the *reservation* taken before the consent prompt commits that hold. The guessing was
        already charged and capped at reservation time, so this path does not charge again — the
        caller has demonstrably held a valid code since then.

        Returns the whole reconciliation rather than just the alias it claimed, so linking can describe
        its outcome in the same words as a refresh — including the contested case, which the alias
        alone cannot express.
        """
        if reservation is None:
            await self.guard_verification_attempts(attempted_by)
        result = await self._repository.consume_code_and_link_account(
            account_id=account_id,
            code=code,
            consent=consent,
            reservation_token=None if reservation is None else reservation.token,
        )
        if result.reservation_expired:
            raise LinkReservationExpiredError
        if result.account is None:
            # A wrong code is the only failure that counts: the conflicts below prove the caller
            # held a *correct* code, so charging them for it would let anyone lock out a user
            # whose account is already linked.
            if reservation is None:
                await self._record_verification_failure(attempted_by)
            raise InvalidVerificationCodeError
        if result.conflicting_java_uuid is not None:
            raise AccountAlreadyLinkedError(
                account_id=account_id,
                minecraft_uuid=result.conflicting_java_uuid,
            )
        await self._repository.clear_verification_failures(*attempted_by)
        if result.refresh is None:
            # The repository sets `refresh` on every path that consumed a code, so this is a broken
            # adapter rather than a reachable state.
            message = "A consumed verification code produced no identity reconciliation."
            raise InvalidStateError(message)
        return result.refresh

    async def _record_verification_failure(self, attempted_by: tuple[IdentityProvider, str]) -> None:
        """Charge one refused code against an identity's budget and log a resulting lockout."""
        provider, subject = attempted_by
        locked_until = await self._repository.record_verification_failure(
            provider,
            subject,
            max_failures=VERIFICATION_MAX_CONSECUTIVE_FAILURES,
            lockout_seconds=VERIFICATION_LOCKOUT_SECONDS,
        )
        if locked_until is not None:
            logger.warning(
                "verification.lockout_started",
                extra={"provider": provider.value, "locked_until": str(locked_until)},
            )

    async def unlink_minecraft_account(self, account_id: int) -> bool:
        """Unlink every Java identity from an account."""
        return await self._repository.unlink_java_identity(account_id)

    async def refresh_java_identity(self, account_id: int, *, java_uuid: UUID | None = None) -> IdentityRefresh:
        """Re-read the linked Java name from Mojang and reconcile the creator credit.

        Keyed on the account rather than a Discord ID: this is reachable from Discord, from an
        API session, and from a CLI device, and only the first of those has a Discord ID.

        Raises `MinecraftAccountNotFoundError` when no Java identity is linked or the UUID no
        longer resolves, and `MinecraftServiceUnavailableError` when Mojang cannot be reached.
        """
        account = await self._repository.get_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        identity = next(
            (
                candidate
                for candidate in account.identities
                if candidate.provider is IdentityProvider.JAVA
                and (java_uuid is None or candidate.java_uuid == java_uuid)
            ),
            None,
        )
        if identity is None or identity.java_uuid is None:
            raise NoLinkedMinecraftAccountError(account_id=account_id)
        username = await self._minecraft_username_lookup(identity.java_uuid)
        if username is None:
            raise MinecraftAccountNotFoundError(identity.java_uuid)
        return await self._repository.refresh_java_identity(
            account_id=account_id,
            java_uuid=identity.java_uuid,
            username=username,
        )

    async def request_alias_claim(self, account_id: int, name: str) -> AliasClaim:
        """Open a staff-reviewed request to be credited under a creator name."""
        account = await self._repository.get_by_id(account_id)
        if account is None or account.id is None:
            raise AccountNotFoundError(account_id)
        if account.needs_consent_refresh:
            raise ConsentRequiredError(account_id=account_id)
        return await self._repository.request_claim(name=name, account_id=account.id)

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List creator credit claims awaiting staff review.

        *with_claimants* loads each claimant's account for presentation, at a fixed cost
        rather than one query per claim.
        """
        return await self._repository.pending_claims(with_claimants=with_claimants)

    async def approve_alias_claim(self, claim_id: int, *, staff_account_id: int, reassign: bool = False) -> AliasClaim:
        """Credit the claimant with the alias and close the request.

        *reassign* is required to take a name that is currently credited to someone else, which
        is what a rename into a contested name produces.
        """
        return await self._resolve_claim(claim_id, ClaimStatus.APPROVED, staff_account_id, reassign=reassign)

    async def reject_alias_claim(self, claim_id: int, *, staff_account_id: int) -> AliasClaim:
        """Close the request without crediting the claimant."""
        return await self._resolve_claim(claim_id, ClaimStatus.REJECTED, staff_account_id)

    async def _resolve_claim(
        self, claim_id: int, status: ClaimStatus, staff_account_id: int, *, reassign: bool = False
    ) -> AliasClaim:
        claim = await self._repository.get_claim(claim_id)
        if claim is None or claim.status is not ClaimStatus.PENDING:
            raise ClaimNotFoundError(claim_id)
        return await self._repository.resolve_claim(
            claim_id=claim_id,
            status=status,
            resolved_by_account_id=staff_account_id,
            reassign=reassign,
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
