"""Account application services: one account may hold Discord and Java identities alike."""

import base64
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from whenever import Instant

from squid.accounts.application.ports import AccountUnitOfWork
from squid.accounts.domain import (
    MERGE_CODE_BYTES,
    MERGE_TICKET_TTL_SECONDS,
    AccountConsent,
    AccountMerge,
    ClaimStatus,
    CreatorProfile,
    IdentityProvider,
    IdentityRefresh,
    LinkReservation,
    MergePreview,
    ProfileUpdate,
    PublicCreatorProfile,
    RecentAccountProof,
    present_public_profile,
)
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountIdentityNotFoundError,
    AccountNotFoundError,
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidMergeCodeError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    LinkReservationExpiredError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
    VerificationAttemptsExhaustedError,
)
from squid.accounts.models import Account, AccountIdentity, AccountProfile, AliasClaim, CreatorAlias, MergeTicket
from squid.core.errors import ConflictError, InvalidStateError
from squid.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)

VERIFICATION_MAX_CONSECUTIVE_FAILURES = 5
"""Number of consecutive wrong codes that triggers an identity's lockout."""

VERIFICATION_LOCKOUT_SECONDS = 15 * 60
"""Longer than a code lives, so a lockout always outlasts the codes it was protecting."""

VERIFICATION_CODE_DIGITS = 10

VERIFICATION_CODE_ATTEMPTS = 5
"""How many fresh codes to try when each collides with a valid code another player holds."""

LINK_RESERVATION_TTL_SECONDS = 180
"""Reservation lifetime, in seconds, for a code awaiting consent.

This exceeds the Discord prompt's 120-second timeout. The code's own expiry is
still checked when linking, even if the reservation has time left.
"""


def generate_verification_code() -> int:
    """Return a random ten-digit verification code.

    The in-game plugin displays an integer, so the code stays numeric. Redemption
    looks up the code across all outstanding identities, making the attempt limit
    and expiry important even with roughly 33 bits of randomness.
    """
    lower = 10 ** (VERIFICATION_CODE_DIGITS - 1)
    return secrets.randbelow(9 * lower) + lower


class AccountService:
    """Manage accounts, their sign-in identities, profiles, and creator credit.

    Callers establish which account is acting; this service coordinates consent,
    verification-code linking, and staff review of contested creator aliases.
    A verification code proves a Java identity. A reservation lets a consent
    prompt preview that link before redemption. A merge code instead authorizes
    combining two accounts that already exist.

    Repository writes and their related account changes commit together through
    the unit of work. External username lookups happen before those writes.
    """

    def __init__(
        self,
        minecraft_username_lookup: Callable[[UUID], Awaitable[str | None]],
        verification_code_factory: Callable[[], int],
        *,
        uow_factory: UnitOfWorkFactory[AccountUnitOfWork],
    ):
        self._uow_factory = uow_factory
        self._minecraft_username_lookup = minecraft_username_lookup
        self._verification_code_factory = verification_code_factory

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        """Return the account holding one canonical provider subject, or ``None``."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_by_identity(provider, subject)

    async def get_account_by_id(self, account_id: int) -> Account | None:
        """Return an account by its internal identifier."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_by_id(account_id)

    async def get_accounts(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return accounts with their identities, omitting unknown IDs and batching the reads."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_many(account_ids)

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        """Resolve a verified provider subject, creating an account if needed.

        The caller must have proof of the subject, such as a completed OAuth exchange.
        Merely observing an ID is not proof. A supplied consent receipt is saved with
        a newly created account; existing accounts keep their current receipt.
        """
        async with self._uow_factory() as uow:
            result = await uow.accounts.get_or_create_identity(provider, subject, consent=consent)
            await uow.commit()
            return result

    async def grant_current_consent(self, account_id: int) -> Account:
        """Record consent for any authenticated caller, however they proved their identity."""
        async with self._uow_factory() as uow:
            result = await uow.accounts.update_consent(account_id, AccountConsent.grant_current())
            await uow.commit()
            return result

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
        async with self._uow_factory() as uow:
            result = await uow.accounts.merge(surviving_proof.account_id, absorbed_proof.account_id)
            await uow.commit()
            return result

    async def create_merge_code(self, account_id: int) -> tuple[str, MergeTicket]:
        """Create a single-use code for the account that will be absorbed in a merge.

        The caller must authenticate that account. Creation replaces any earlier ticket
        and records the proof timestamp used at redemption. The plaintext code is
        returned once; only its digest is stored.
        """
        async with self._uow_factory() as uow:
            code = base64.b32encode(secrets.token_bytes(MERGE_CODE_BYTES)).decode("ascii").rstrip("=")
            # The pepper stays in infrastructure: the repository hashes, exactly as it does for a
            # verification code, so no application-layer value is ever the stored one.
            ticket = await uow.accounts.replace_merge_ticket(account_id, code, MERGE_TICKET_TTL_SECONDS)
            result = code, ticket
            await uow.commit()
            return result

    async def preview_merge(self, surviving_account_id: int, code: str) -> MergePreview:
        """Describe the account and credits a code would merge, without consuming the code.

        Show this before confirmation: completion deletes the absorbed account and
        turns its public creator ID into a permanent redirect.
        """
        async with self._uow_factory() as uow:
            ticket = await uow.accounts.peek_merge_ticket(code)
            if ticket is None or ticket.account_id == surviving_account_id:
                # Self-merge is refused here as well as in `merge_accounts`, so the preview never
                # renders a confirmation for something that cannot commit.
                raise InvalidMergeCodeError
            absorbed = await uow.accounts.get_by_id(ticket.account_id)
            if absorbed is None or absorbed.public_creator_id is None:
                raise InvalidMergeCodeError
            record = await uow.accounts.get_creator_profile_record(absorbed.public_creator_id)
            aliases = () if record is None else record.aliases
            return MergePreview(
                absorbed_public_creator_id=absorbed.public_creator_id,
                alias_names=tuple(alias.name for alias in aliases),
                identity_count=len(absorbed.identities),
                build_count=sum(alias.build_count for alias in aliases),
            )

    async def complete_merge(self, surviving_account_id: int, code: str, *, now: Instant | None = None) -> AccountMerge:
        """Merge the account named by the code into the caller's account.

        The caller must authenticate the surviving account. The ticket's creation time
        supplies recent proof for the absorbed account. Ticket consumption and the merge
        share a transaction, so a failed merge does not spend the code.
        """
        async with self._uow_factory() as uow:
            ticket = await uow.accounts.consume_merge_ticket(code)
            checked_at = now or Instant.now()
            if ticket is None:
                raise InvalidMergeCodeError
            if ticket.account_id == surviving_account_id or not RecentAccountProof(
                ticket.account_id, ticket.created_at
            ).is_recent_at(checked_at):
                raise InvalidMergeProofError
            result = await uow.accounts.merge(surviving_account_id, ticket.account_id)
            await uow.commit()
            return result

    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        """Return a creator credit by name without loading its linked account."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_alias_by_name(name)

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        """Return a public creator profile, following permanent merge redirects."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_creator_profile(public_id)

    async def guard_verification_attempts(self, attempted_by: tuple[IdentityProvider, str]) -> None:
        """Reject a code attempt while the external identity is locked out.

        Reservations and direct redemption share this check, so previewing a code
        cannot bypass the guessing limit enforced when linking it.
        """
        async with self._uow_factory() as uow:
            provider, subject = attempted_by
            locked_until = await uow.accounts.verification_lockout(provider, subject)
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
        """Reserve a valid code so a consent prompt can preview the link.

        Apply the same guessing limit as direct redemption and count rejected codes as
        failures. A reservation records only a token and expiry on the code row.
        """
        await self.guard_verification_attempts(attempted_by)
        async with self._uow_factory() as uow:
            reservation = await uow.accounts.reserve_verification_code(code, ttl_seconds=ttl_seconds)
            await uow.commit()
        if reservation is None:
            await self._record_verification_failure(attempted_by)
            raise InvalidVerificationCodeError
        return reservation

    async def release_minecraft_link(self, code: str, reservation: LinkReservation) -> None:
        """Give a held code back after a declined or abandoned prompt.

        Best-effort by design: if this never runs, `reserved_until` lapses on its own, so a crash
        costs one prompt's delay rather than a permanently stuck code.
        """
        async with self._uow_factory() as uow:
            await uow.accounts.release_verification_code(code, reservation.token)
            await uow.commit()

    async def link_minecraft_account(
        self,
        account_id: int,
        code: str,
        *,
        consent: AccountConsent,
        attempted_by: tuple[IdentityProvider, str],
        reservation: LinkReservation | None = None,
    ) -> IdentityRefresh:
        """Link a verified Java identity and reconcile its creator credit.

        The target account must exist. attempted_by identifies the external identity
        whose wrong guesses count toward a lockout. A reservation has already passed
        that check, so redeeming its token does not charge another attempt.

        The result includes retained aliases and any contested name sent for staff
        review. Conflicting Java links are rejected without consuming the code.
        """
        if reservation is None:
            await self.guard_verification_attempts(attempted_by)
        async with self._uow_factory() as uow:
            result = await uow.accounts.consume_code_and_link_account(
                account_id=account_id,
                code=code,
                consent=consent,
                reservation_token=None if reservation is None else reservation.token,
            )
            if result.account is not None and result.conflicting_java_uuid is None:
                await uow.accounts.clear_verification_failures(*attempted_by)
            await uow.commit()
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
        if result.refresh is None:
            # The repository sets `refresh` on every path that consumed a code, so this is a broken
            # adapter rather than a reachable state.
            message = "A consumed verification code produced no identity reconciliation."
            raise InvalidStateError(message)
        return result.refresh

    async def _record_verification_failure(self, attempted_by: tuple[IdentityProvider, str]) -> None:
        """Charge one refused code against an identity's budget and log a resulting lockout."""
        async with self._uow_factory() as uow:
            provider, subject = attempted_by
            locked_until = await uow.accounts.record_verification_failure(
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
            await uow.commit()

    async def list_identities(self, account_id: int) -> tuple[AccountIdentity, ...]:
        """Return every identity linked to the account, including hidden ones."""
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_by_id(account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            return account.identities

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity:
        """Remove one linked identity, refusing to strand the account without any.

        Creator credit is deliberately untouched: aliases stay claimed, because a build's
        attribution is a fact about the build and should not change when someone tidies up how
        they sign in.
        """
        async with self._uow_factory() as uow:
            removed = await uow.accounts.unlink_identity(account_id, identity_id)
            if removed is None:
                raise AccountIdentityNotFoundError(identity_id, account_id=account_id)
            await uow.commit()
            return removed

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one linked identity on the account's public profile."""
        async with self._uow_factory() as uow:
            result = await uow.accounts.set_identity_visibility(account_id, identity_id, is_public=is_public)
            await uow.commit()
            return result

    async def record_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Store the provider key an avatar URL needs, when a caller happens to hold a fresh one."""
        async with self._uow_factory() as uow:
            await uow.accounts.set_identity_avatar_key(account_id, identity_id, avatar_key)
            await uow.commit()

    async def get_profile(self, account_id: int) -> AccountProfile:
        """Return an account's own profile, including anything it has chosen to hide."""
        async with self._uow_factory() as uow:
            profile = await uow.accounts.get_profile(account_id)
            if profile is None:
                raise AccountNotFoundError(account_id)
            return profile

    async def update_profile(self, account_id: int, update: ProfileUpdate) -> AccountProfile:
        """Validate and save a partial profile edit after checking the account's consent."""
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_by_id(account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            if account.needs_consent_refresh:
                raise ConsentRequiredError(account_id=account_id)
            result = await uow.accounts.upsert_profile(account_id, update.validated())
            await uow.commit()
            return result

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Clear profile text, links, and avatar, and make the profile visible.

        The caller must authorize this moderation action. Creator aliases and linked
        identities are retained.
        """
        async with self._uow_factory() as uow:
            result = await uow.accounts.clear_profile(account_id)
            await uow.commit()
            return result

    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        """Return what a stranger may see of one creator, following merge redirects."""
        async with self._uow_factory() as uow:
            record = await uow.accounts.get_creator_profile_record(public_id)
            return None if record is None else present_public_profile(record)

    async def refresh_java_identity(self, account_id: int, *, java_uuid: UUID | None = None) -> IdentityRefresh:
        """Fetch a linked Java identity's current name from Mojang and reconcile its credit.

        An explicit java_uuid selects that identity. Otherwise choose the most recently
        verified Java identity, breaking ties by persistence order.

        Raise NoLinkedMinecraftAccountError if the account has no matching identity,
        MinecraftAccountNotFoundError if Mojang has no profile for it, or
        MinecraftServiceUnavailableError if the lookup fails.
        """
        account = await self.get_account_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        identity = (
            account.most_recent_identity_for(IdentityProvider.JAVA)
            if java_uuid is None
            else account.identity_for(IdentityProvider.JAVA, str(java_uuid))
        )
        if identity is None or identity.java_uuid is None:
            raise NoLinkedMinecraftAccountError(account_id=account_id)
        username = await self._minecraft_username_lookup(identity.java_uuid)
        if username is None:
            raise MinecraftAccountNotFoundError(identity.java_uuid)
        async with self._uow_factory() as uow:
            result = await uow.accounts.refresh_java_identity(
                account_id=account_id,
                java_uuid=identity.java_uuid,
                username=username,
            )
            await uow.commit()
            return result

    async def request_alias_claim(self, account_id: int, name: str) -> AliasClaim:
        """Open a staff-reviewed request to be credited under a creator name."""
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_by_id(account_id)
            if account is None or account.id is None:
                raise AccountNotFoundError(account_id)
            if account.needs_consent_refresh:
                raise ConsentRequiredError(account_id=account_id)
            try:
                result = await uow.accounts.request_claim(name=name, account_id=account.id)
            except AliasAlreadyClaimedError as conflict:
                raise await self._name_the_holder(uow, conflict) from None
            await uow.commit()
            return result

    async def _name_the_holder(
        self, uow: AccountUnitOfWork, conflict: AliasAlreadyClaimedError
    ) -> AliasAlreadyClaimedError:
        """Resolve a conflicting holder's public creator name, when there is one to resolve.

        Enriched here rather than in the repository so the extra query happens only on the error
        path, and only where a human is going to read the result. A profile with no aliases yet
        leaves the message as it was rather than naming an empty string.
        """
        if conflict.holder_public_creator_id is None:
            return conflict
        profile = await uow.accounts.get_creator_profile(conflict.holder_public_creator_id)
        if profile is None or not profile.aliases:
            return conflict
        return conflict.with_holder_name(profile.aliases[0])

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List creator credit claims awaiting staff review.

        *with_claimants* loads each claimant's account for presentation, at a fixed cost
        rather than one query per claim.
        """
        async with self._uow_factory() as uow:
            return await uow.accounts.pending_claims(with_claimants=with_claimants)

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
        async with self._uow_factory() as uow:
            claim = await uow.accounts.get_claim(claim_id)
            if claim is None or claim.status is not ClaimStatus.PENDING:
                raise ClaimNotFoundError(claim_id)
            try:
                result = await uow.accounts.resolve_claim(
                    claim_id=claim_id,
                    status=status,
                    resolved_by_account_id=staff_account_id,
                    reassign=reassign,
                )
            except AliasAlreadyClaimedError as conflict:
                # A reviewer needs to know who holds the name they are being stopped from granting.
                raise await self._name_the_holder(uow, conflict) from None
            await uow.commit()
            return result

    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
        """Generate a verification code after validating the Java account."""
        minecraft_username = await self._minecraft_username_lookup(minecraft_uuid)
        if minecraft_username is None:
            raise MinecraftAccountNotFoundError(minecraft_uuid)
        async with self._uow_factory() as uow:
            # Codes are short, so a fresh one can collide with a code another player still holds.
            for _ in range(VERIFICATION_CODE_ATTEMPTS):
                code = self._verification_code_factory()
                if await uow.accounts.replace_verification_code(
                    minecraft_uuid=minecraft_uuid,
                    code=str(code),
                    username=minecraft_username,
                ):
                    await uow.commit()
                    return code
        msg = f"No unused verification code found after {VERIFICATION_CODE_ATTEMPTS} attempts."
        raise ConflictError(msg)
