"""Account application services: one account may hold Discord and Java identities alike."""

import base64
import logging
import secrets
from collections.abc import Awaitable, Callable, Collection, Sequence
from uuid import UUID

from whenever import Instant

from squid.core.accounts.application.edits import AccountEditorSnapshot
from squid.core.accounts.application.ports import AccountUnitOfWork
from squid.core.accounts.domain import (
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
    PublicCreatorProfile,
    RecentAccountProof,
)
from squid.core.accounts.errors import (
    AccountAlreadyLinkedError,
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
from squid.core.accounts.models import (
    Account,
    AccountIdentity,
    AccountProfile,
    AliasClaim,
    CataloguePreference,
    CreatorAlias,
    MergeTicket,
)
from squid.core.actors import ActingContext, Actor, application_operation
from squid.core.errors import ConflictError, InvalidStateError
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
    ACCOUNT_PROFILE_MODERATE,
    ACCOUNT_SELF_MANAGE,
    ACCOUNT_SELF_READ,
    ACCOUNT_VERIFY_RELAY,
)
from squid.core.unit_of_work import UnitOfWorkFactory

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
        self._permissions = PermissionService(uow_factory=uow_factory)
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

    @application_operation
    async def catalogue_preferences(self, *, context: ActingContext) -> Sequence[CataloguePreference]:
        async with self._uow_factory() as uow:
            return await uow.accounts.catalogue_preferences(context.effective.require_account_id())

    @application_operation
    async def save_catalogue_preference(self, *, scope: str, filters: str, sort: str, context: ActingContext) -> None:
        async with self._uow_factory() as uow:
            await uow.accounts.save_catalogue_preference(
                CataloguePreference(
                    account_id=context.effective.require_account_id(), scope=scope, filters=filters, sort=sort
                )
            )
            await uow.commit()

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

    async def grant_current_consent(self, *, context: ActingContext) -> Account:
        """Record consent for any authenticated caller, however they proved their identity."""
        account_id = context.effective.require_account_id()
        with context.scope():
            async with self._uow_factory() as uow:
                await self._permissions.require(context.effective, ACCOUNT_SELF_READ, uow=uow)
                result = await uow.accounts.update_consent(account_id, AccountConsent.grant_current())
                await uow.commit()
                return result

    @application_operation
    async def merge_accounts(
        self,
        surviving_proof: RecentAccountProof,
        absorbed_proof: RecentAccountProof,
        *,
        context: ActingContext,
        now: Instant | None = None,
    ) -> AccountMerge:
        """Merge two accounts only after recent, independent proof of both."""
        checked_at = now or Instant.now()
        if (
            context.effective.require_account_id() != surviving_proof.account_id
            or surviving_proof.account_id == absorbed_proof.account_id
            or not surviving_proof.is_recent_at(checked_at)
            or not absorbed_proof.is_recent_at(checked_at)
        ):
            raise InvalidMergeProofError
        async with (
            self._uow_factory() as uow,
            uow.builds.editing(actor=surviving_proof.account_id, operation="account_merge", source="account_merge"),
        ):
            result = await uow.accounts.merge(surviving_proof.account_id, absorbed_proof.account_id)
            await uow.commit()
            return result

    async def create_merge_code(self, *, context: ActingContext) -> tuple[str, MergeTicket]:
        """Create a single-use code for the account that will be absorbed in a merge.

        The caller must authenticate that account. Creation replaces any earlier ticket
        and records the proof timestamp used at redemption. The plaintext code is
        returned once; only its digest is stored.
        """
        account_id = context.effective.require_account_id()
        with context.scope():
            async with self._uow_factory() as uow:
                await self._permissions.require(context.effective, ACCOUNT_SELF_MANAGE, uow=uow)
                code = base64.b32encode(secrets.token_bytes(MERGE_CODE_BYTES)).decode("ascii").rstrip("=")
                # The pepper stays in infrastructure: the repository hashes, exactly as it does for a
                # verification code, so no application-layer value is ever the stored one.
                ticket = await uow.accounts.replace_merge_ticket(account_id, code, MERGE_TICKET_TTL_SECONDS)
                result = code, ticket
                await uow.commit()
                return result

    async def preview_merge(self, code: str, *, context: ActingContext) -> MergePreview:
        """Describe the account and credits a code would merge, without consuming the code.

        Show this before confirmation: completion deletes the absorbed account and
        turns its public creator ID into a permanent redirect.
        """
        surviving_account_id = context.effective.require_account_id()
        with context.scope():
            async with self._uow_factory() as uow:
                await self._permissions.require(context.effective, ACCOUNT_SELF_MANAGE, uow=uow)
                ticket = await uow.accounts.peek_merge_ticket(code)
                if ticket is None or ticket.account_id == surviving_account_id:
                    # Self-merge is refused here as well as in `merge_accounts`, so the preview never
                    # renders a confirmation for something that cannot commit.
                    raise InvalidMergeCodeError
                absorbed = await uow.accounts.get_by_id(ticket.account_id)
                # ORM identity columns may still be None before flush.
                if absorbed is None or absorbed.public_account_id is None:  # pyright: ignore[reportUnnecessaryComparison]
                    raise InvalidMergeCodeError
                record = await uow.accounts.get_creator_profile_record(absorbed.public_account_id)
                aliases = () if record is None else record.aliases
                return MergePreview(
                    absorbed_public_account_id=absorbed.public_account_id,
                    alias_names=tuple(alias.name for alias in aliases),
                    identity_count=len(absorbed.identities),
                    build_count=sum(alias.build_count for alias in aliases),
                )

    async def complete_merge(self, code: str, *, context: ActingContext, now: Instant | None = None) -> AccountMerge:
        """Merge the account named by the code into the caller's account.

        The caller must authenticate the surviving account. The ticket's creation time
        supplies recent proof for the absorbed account. Ticket consumption and the merge
        share a transaction, so a failed merge does not spend the code.
        """
        surviving_account_id = context.effective.require_account_id()
        with context.scope():
            async with (
                self._uow_factory() as uow,
                uow.builds.editing(actor=surviving_account_id, operation="account_merge", source="account_merge"),
            ):
                await self._permissions.require(context.effective, ACCOUNT_SELF_MANAGE, uow=uow)
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

    @application_operation
    async def get_creator_alias(self, alias: int | str, *, context: ActingContext) -> CreatorAlias | None:
        """Return a creator credit by ID or by name without loading its linked account."""
        async with self._uow_factory() as uow:
            if isinstance(alias, int):
                return await uow.accounts.get_alias(alias)
            return await uow.accounts.get_alias_by_name(alias)

    @application_operation
    async def resolve_public_id(self, public_id: UUID, *, context: ActingContext) -> int:
        """Return the account a public ID names, following merge redirects.

        Raises AccountNotFoundError for an ID that never existed.
        """
        async with self._uow_factory() as uow:
            account_id = await uow.accounts.account_id_for_public_id(public_id)
        if account_id is None:
            raise AccountNotFoundError(public_id=public_id)
        return account_id

    @application_operation
    async def public_ids(self, account_ids: Collection[int], *, context: ActingContext) -> dict[int, UUID]:
        """Return the public ID of each existing account, for responses that name internal accounts."""
        async with self._uow_factory() as uow:
            return await uow.accounts.public_ids_for(account_ids)

    @application_operation
    async def get_creator_profile(self, public_id: UUID, *, context: ActingContext) -> CreatorProfile | None:
        """Return a public creator profile, following permanent merge redirects."""
        async with self._uow_factory() as uow:
            return await uow.accounts.get_creator_profile(public_id)

    async def _guard_verification_attempts(self, attempted_by: tuple[IdentityProvider, str]) -> None:
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

    @application_operation
    async def reserve_minecraft_link(
        self,
        code: str,
        *,
        context: ActingContext,
        ttl_seconds: int = LINK_RESERVATION_TTL_SECONDS,
    ) -> LinkReservation:
        """Reserve a valid code so a consent prompt can preview the link.

        Apply the same guessing limit as direct redemption and count rejected codes as
        failures. A reservation records only a token and expiry on the code row.
        """
        attempted_by = (IdentityProvider.DISCORD, str(context.effective.require_discord_user_id()))
        await self._guard_verification_attempts(attempted_by)
        async with self._uow_factory() as uow:
            reservation = await uow.accounts.reserve_verification_code(code, ttl_seconds=ttl_seconds)
            await uow.commit()
        if reservation is None:
            await self._record_verification_failure(attempted_by)
            raise InvalidVerificationCodeError
        return reservation

    @application_operation
    async def release_minecraft_link(self, code: str, reservation: LinkReservation, *, context: ActingContext) -> None:
        """Give a held code back after a declined or abandoned prompt.

        Best-effort by design: if this never runs, `reserved_until` lapses on its own, so a crash
        costs one prompt's delay rather than a permanently stuck code.
        """
        async with self._uow_factory() as uow:
            await uow.accounts.release_verification_code(code, reservation.token)
            await uow.commit()

    @application_operation
    async def link_minecraft_account(
        self,
        code: str,
        *,
        consent: AccountConsent,
        context: ActingContext,
        reservation: LinkReservation | None = None,
    ) -> IdentityRefresh:
        """Link a verified Java identity and reconcile its creator credit.

        The target account must exist. attempted_by identifies the external identity
        whose wrong guesses count toward a lockout. A reservation has already passed
        that check, so redeeming its token does not charge another attempt.

        The result includes retained aliases and any contested name sent for staff
        review. Conflicting Java links are rejected without consuming the code.
        """
        account_id = context.effective.require_account_id()
        attempted_by = (IdentityProvider.DISCORD, str(context.effective.require_discord_user_id()))
        if reservation is None:
            await self._guard_verification_attempts(attempted_by)
        async with (
            self._uow_factory() as uow,
            uow.builds.editing(actor=account_id, operation="edit", source="identity_link"),
        ):
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

    async def list_identities(self, *, context: ActingContext) -> tuple[AccountIdentity, ...]:
        """Return every identity linked to the account, including hidden ones."""
        account_id = context.effective.require_account_id()
        with context.scope():
            async with self._uow_factory() as uow:
                account = await uow.accounts.get_by_id(account_id)
                if account is None:
                    raise AccountNotFoundError(account_id)
                return account.identities

    @application_operation
    async def record_identity_avatar_key(
        self, identity_id: int, avatar_key: str | None, *, context: ActingContext
    ) -> None:
        """Store the provider key an avatar URL needs, when a caller happens to hold a fresh one."""
        async with self._uow_factory() as uow:
            await uow.accounts.set_identity_avatar_key(context.effective.require_account_id(), identity_id, avatar_key)
            await uow.commit()

    @application_operation
    async def editor_snapshot(self, account_id: int, *, context: ActingContext) -> AccountEditorSnapshot:
        """Read the profile and identities under their shared account revision lock."""
        async with self._uow_factory() as uow:
            required = (
                ACCOUNT_SELF_READ if account_id == context.effective.linked_account_id else ACCOUNT_PROFILE_MODERATE
            )
            await self._permissions.require(context.effective, required, uow=uow)
            account = await uow.accounts.lock_for_edit(account_id)
            profile = await uow.accounts.get_profile(account_id)
            assert profile is not None
            return AccountEditorSnapshot(account, profile)

    @application_operation
    async def get_profile(self, account_id: int, *, context: ActingContext) -> AccountProfile:
        """Return an account's own profile, including anything it has chosen to hide."""
        async with self._uow_factory() as uow:
            required = (
                ACCOUNT_SELF_READ if account_id == context.effective.linked_account_id else ACCOUNT_PROFILE_MODERATE
            )
            await self._permissions.require(context.effective, required, uow=uow)
            profile = await uow.accounts.get_profile(account_id)
            if profile is None:
                raise AccountNotFoundError(account_id)
            return profile

    @application_operation
    async def get_public_profile(self, public_id: UUID, *, context: ActingContext) -> PublicCreatorProfile | None:
        """Return what a stranger may see of one creator, following merge redirects."""
        async with self._uow_factory() as uow:
            record = await uow.accounts.get_creator_profile_record(public_id)
            return None if record is None else PublicCreatorProfile.from_record(record)

    @application_operation
    async def credit_owners(self, build_id: int, *, context: ActingContext) -> dict[int, UUID]:
        """Map each credit position on a build to the public ID of the creator it belongs to.

        Unclaimed names and credits matching only a private identity are absent.
        """
        async with self._uow_factory() as uow:
            return await uow.accounts.credit_owners(build_id)

    async def refresh_java_identity(
        self, account_id: int, *, context: ActingContext, java_uuid: UUID | None = None
    ) -> IdentityRefresh:
        """Fetch a linked Java identity's current name from Mojang and reconcile its credit.

        An explicit java_uuid selects that identity. Otherwise choose the most recently
        verified Java identity, breaking ties by persistence order.

        Raise NoLinkedMinecraftAccountError if the account has no matching identity,
        MinecraftAccountNotFoundError if Mojang has no profile for it, or
        MinecraftServiceUnavailableError if the lookup fails.
        """
        subject = context.effective
        permission = (
            ACCOUNT_IDENTITY_REFRESH if subject.linked_account_id == account_id else ACCOUNT_IDENTITY_REFRESH_ANY
        )
        with context.scope():
            await self._permissions.require(subject, permission)
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
            async with (
                self._uow_factory() as uow,
                uow.builds.editing(
                    actor=subject.linked_account_id if subject.linked_account_id is not None else subject.reference,
                    operation="edit",
                    source="identity_refresh",
                ),
            ):
                await self._permissions.require(subject, permission, uow=uow)
                result = await uow.accounts.refresh_java_identity(
                    account_id=account_id,
                    java_uuid=identity.java_uuid,
                    username=username,
                )
                await uow.commit()
                return result

    async def request_alias_claim(self, name: str, *, context: ActingContext) -> AliasClaim:
        """Open a staff-reviewed request to be credited under a creator name."""
        account_id = context.effective.require_account_id()
        with context.scope():
            async with (
                self._uow_factory() as uow,
                uow.builds.editing(actor=account_id, operation="edit", source="alias_claim"),
            ):
                account = await uow.accounts.get_by_id(account_id)
                if account is None or account.id is None:  # pyright: ignore[reportUnnecessaryComparison]
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
        if conflict.holder_public_account_id is None:
            return conflict
        profile = await uow.accounts.get_creator_profile(conflict.holder_public_account_id)
        if profile is None or not profile.aliases:
            return conflict
        return conflict.with_holder_name(profile.aliases[0])

    async def pending_alias_claims(
        self, *, context: ActingContext, with_claimants: bool = False
    ) -> Sequence[AliasClaim]:
        """List creator credit claims awaiting staff review.

        *with_claimants* loads each claimant's account for presentation, at a fixed cost
        rather than one query per claim.
        """
        with context.scope():
            async with self._uow_factory() as uow:
                await self._permissions.require(context.effective, ACCOUNT_CLAIM_LIST, uow=uow)
                return await uow.accounts.pending_claims(with_claimants=with_claimants)

    async def approve_alias_claim(self, claim_id: int, *, context: ActingContext, reassign: bool = False) -> AliasClaim:
        """Credit the claimant with the alias and close the request.

        *reassign* is required to take a name that is currently credited to someone else, which
        is what a rename into a contested name produces.
        """
        with context.scope():
            return await self._resolve_claim(claim_id, ClaimStatus.APPROVED, context.effective, reassign=reassign)

    async def reject_alias_claim(self, claim_id: int, *, context: ActingContext) -> AliasClaim:
        """Close the request without crediting the claimant."""
        with context.scope():
            return await self._resolve_claim(claim_id, ClaimStatus.REJECTED, context.effective)

    async def _resolve_claim(
        self, claim_id: int, status: ClaimStatus, actor: Actor, *, reassign: bool = False
    ) -> AliasClaim:
        staff_account_id = actor.require_account_id()
        async with (
            self._uow_factory() as uow,
            uow.builds.editing(actor=staff_account_id, operation="edit", source="alias_claim"),
        ):
            node = ACCOUNT_CLAIM_APPROVE if status is ClaimStatus.APPROVED else ACCOUNT_CLAIM_REJECT
            await self._permissions.require(actor, node, uow=uow)
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

    @application_operation
    async def generate_verification_code(self, minecraft_uuid: UUID, *, context: ActingContext) -> int:
        """Generate a verification code after validating the Java account."""
        await self._permissions.require(context.effective, ACCOUNT_VERIFY_RELAY)
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
