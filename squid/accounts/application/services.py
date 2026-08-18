"""Account application services: one account may hold Discord and Java identities alike."""

import base64
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from whenever import Instant

from squid.accounts.application.ports import AccountRepository
from squid.accounts.domain import (
    MERGE_CODE_BYTES,
    MERGE_TICKET_TTL_SECONDS,
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    IdentityProvider,
    IdentityRefresh,
    LinkReservation,
    MergePreview,
    MergeTicket,
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
    LastIdentityError,
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

    async def create_merge_code(self, account_id: int) -> tuple[str, MergeTicket]:
        """Mint a single-use code offering this account up to be absorbed.

        Called on the account that is going *away*. That direction is deliberate: minting is an
        act of consent by the side that loses its public creator id, and it is the side that must
        prove it authenticated recently, since the surviving side proves that by redeeming.

        The plaintext is returned once and never stored.
        """
        code = base64.b32encode(secrets.token_bytes(MERGE_CODE_BYTES)).decode("ascii").rstrip("=")
        # The pepper stays in infrastructure: the repository hashes, exactly as it does for a
        # verification code, so no application-layer value is ever the stored one.
        ticket = await self._repository.replace_merge_ticket(account_id, code, MERGE_TICKET_TTL_SECONDS)
        return code, ticket

    async def preview_merge(self, surviving_account_id: int, code: str) -> MergePreview:
        """Describe what redeeming *code* would move, without spending it.

        A merge cannot be undone -- the absorbed public creator id becomes a permanent redirect --
        so both surfaces show this before the irreversible button.
        """
        ticket = await self._repository.peek_merge_ticket(code)
        if ticket is None or ticket.account_id == surviving_account_id:
            # Self-merge is refused here as well as in `merge_accounts`, so the preview never
            # renders a confirmation for something that cannot commit.
            raise InvalidMergeCodeError
        absorbed = await self._repository.get_by_id(ticket.account_id)
        if absorbed is None or absorbed.public_creator_id is None:
            raise InvalidMergeCodeError
        record = await self._repository.get_creator_profile_record(absorbed.public_creator_id)
        aliases = () if record is None else record.aliases
        return MergePreview(
            absorbed_public_creator_id=absorbed.public_creator_id,
            alias_names=tuple(alias.name for alias in aliases),
            identity_count=len(absorbed.identities),
            build_count=sum(alias.build_count for alias in aliases),
        )

    async def complete_merge(self, surviving_account_id: int, code: str, *, now: Instant | None = None) -> AccountMerge:
        """Absorb the account that minted *code* into the caller's.

        Consuming the ticket is what supplies the absorbed side's `RecentAccountProof`: the ticket
        was minted by an authenticated session and lives exactly as long as such a proof is
        accepted, so its creation timestamp is the proof timestamp. The caller authenticating now
        is the surviving side's.
        """
        ticket = await self._repository.consume_merge_ticket(code)
        if ticket is None:
            raise InvalidMergeCodeError
        checked_at = now or Instant.now()
        return await self.merge_accounts(
            RecentAccountProof(surviving_account_id, checked_at),
            RecentAccountProof(ticket.account_id, ticket.created_at),
            now=checked_at,
        )

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
        """Unlink every Java identity from an account.

        Superseded by `unlink_identity`, which removes one identity rather than a whole provider.
        Kept until the bot stops calling it.
        """
        return await self._repository.unlink_java_identity(account_id)

    async def list_identities(self, account_id: int) -> tuple[AccountIdentity, ...]:
        """Return every identity linked to an account, hidden ones included.

        This is the self view, so visibility does not filter it: you can only unhide what you can
        see listed.
        """
        account = await self._repository.get_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account.identities

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity:
        """Remove one linked identity, refusing to strand the account without any.

        Creator credit is deliberately untouched: aliases stay claimed, because a build's
        attribution is a fact about the build and should not change when someone tidies up how
        they sign in.
        """
        if await self._repository.count_identities(account_id) <= 1:
            raise LastIdentityError(account_id=account_id)
        removed = await self._repository.unlink_identity(account_id, identity_id)
        if removed is None:
            raise AccountIdentityNotFoundError(identity_id, account_id=account_id)
        return removed

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one linked identity on the account's public profile."""
        return await self._repository.set_identity_visibility(account_id, identity_id, is_public=is_public)

    async def record_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Store the provider key an avatar URL needs, when a caller happens to hold a fresh one."""
        await self._repository.set_identity_avatar_key(account_id, identity_id, avatar_key)

    async def get_profile(self, account_id: int) -> AccountProfile:
        """Return an account's own profile, including anything it has chosen to hide."""
        profile = await self._repository.get_profile(account_id)
        if profile is None:
            raise AccountNotFoundError(account_id)
        return profile

    async def update_profile(self, account_id: int, update: ProfileUpdate) -> AccountProfile:
        """Apply a partial profile edit after validating it.

        Consent-gated like every other write that publishes something about a person: a profile is
        the most public thing an account owns.
        """
        account = await self._repository.get_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        if account.needs_consent_refresh:
            raise ConsentRequiredError(account_id=account_id)
        return await self._repository.upsert_profile(account_id, update.validated())

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Reset a profile to its empty state, for staff handling abuse.

        Deliberately does not hide the profile: `hidden` belongs to its owner, and a moderation
        action that also flipped it would take that decision away from them.
        """
        return await self._repository.clear_profile(account_id)

    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        """Return what a stranger may see of one creator, following merge redirects."""
        record = await self._repository.get_creator_profile_record(public_id)
        return None if record is None else present_public_profile(record)

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
        try:
            return await self._repository.request_claim(name=name, account_id=account.id)
        except AliasAlreadyClaimedError as conflict:
            raise await self._name_the_holder(conflict) from None

    async def _name_the_holder(self, conflict: AliasAlreadyClaimedError) -> AliasAlreadyClaimedError:
        """Resolve a conflicting holder's public creator name, when there is one to resolve.

        Enriched here rather than in the repository so the extra query happens only on the error
        path, and only where a human is going to read the result. A profile with no aliases yet
        leaves the message as it was rather than naming an empty string.
        """
        if conflict.holder_public_creator_id is None:
            return conflict
        profile = await self._repository.get_creator_profile(conflict.holder_public_creator_id)
        if profile is None or not profile.aliases:
            return conflict
        return conflict.with_holder_name(profile.aliases[0])

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
        try:
            return await self._repository.resolve_claim(
                claim_id=claim_id,
                status=status,
                resolved_by_account_id=staff_account_id,
                reassign=reassign,
            )
        except AliasAlreadyClaimedError as conflict:
            # A reviewer needs to know who holds the name they are being stopped from granting.
            raise await self._name_the_holder(conflict) from None

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
