"""SQLAlchemy account repository, keyed by account rather than by identity provider."""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import case, column, delete, func, literal, or_, select, table, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.accounts.application.ports import VerificationLinkResult
from squid.accounts.domain import (
    AccountConsent,
    AccountMerge,
    ClaimMethod,
    ClaimStatus,
    CreatorProfile,
    CreatorProfileRecord,
    CreditedAlias,
    CreditPreview,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    ProfileUpdate,
    fold_creator_name,
)
from squid.accounts.errors import (
    AccountIdentityNotFoundError,
    AccountNotFoundError,
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    CreatorAliasNotFoundError,
    LastIdentityError,
    MinecraftAccountNotFoundError,
)
from squid.accounts.infrastructure.account_merge import merge_accounts
from squid.accounts.infrastructure.consent import account_consent_current
from squid.accounts.models import (
    Account,
    AccountIdentity,
    AccountProfile,
    AliasClaim,
    CreatorAlias,
    MergeTicket,
    PublicCreatorRedirect,
)
from squid.accounts.models import VerificationAttempt as VerificationAttemptModel
from squid.accounts.models import VerificationCode as VerificationCodeModel
from squid.core.i18n import tr
from squid.persistence.account_merge import AccountMergeContext
from squid.persistence.types import InstantUTC, now

_BUILD_CREDITS = table("build_creators", column("alias_id"))
"""A lightweight handle on the one column of `build_creators` this module reads.

Deliberately not `squid.builds.models.BuildCreator`: importing that mapper pulls the
whole `Build` relationship graph into the accounts context, which both couples the two contexts and
makes importing this module depend on the builds taxonomy being imported first. All that is wanted
here is a count.
"""


def _with_identities(model: Account, identities: Sequence[AccountIdentity]) -> Account:
    model.identities = tuple(identities)
    model.consent = None
    if model.consent_version is not None and model.consented_at is not None:
        model.consent = AccountConsent(version=model.consent_version, granted_at=model.consented_at)
    return model


def _with_creator(alias: CreatorAlias, public_creator_id: uuid.UUID | None = None) -> CreatorAlias:
    alias.public_creator_id = public_creator_id
    return alias


def _with_claimant(claim: AliasClaim, alias_name: str, claimant: Account | None = None) -> AliasClaim:
    claim.alias_name = alias_name
    claim.claimant = claimant
    return claim


class AccountRepository:
    """Persist accounts, external identities, creator aliases, and verification codes."""

    def __init__(self, session: Callable[[], AsyncSession], verification_code_pepper: str):
        self._session = session
        self._verification_code_pepper = verification_code_pepper

    async def has_current_consent(self, account_id: int) -> bool:
        """Return whether an extant account has a complete current consent receipt."""
        session = self._session()
        result = await session.scalar(
            select(Account.id).where(
                Account.id == account_id,
                account_consent_current(),
            )
        )
        return result is not None

    async def can_approve_minecraft_identity(self, *, account_id: int, java_uuid: uuid.UUID) -> bool:
        """Atomically prove current consent and exact verified Java ownership."""
        session = self._session()
        result = await session.scalar(
            select(AccountIdentity.id)
            .join(Account, Account.id == AccountIdentity.account_id)
            .where(
                Account.id == account_id,
                account_consent_current(),
                AccountIdentity.provider == IdentityProvider.JAVA,
                AccountIdentity.subject == str(java_uuid),
                AccountIdentity.verified_at.is_not(None),
            )
        )
        return result is not None

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        """Insert one account and its verified identities atomically."""
        session = self._session()
        account = Account(
            consent_version=None if consent is None else consent.version,
            consented_at=None if consent is None else consent.granted_at,
        )
        session.add(account)
        await session.flush()
        session.add(AccountProfile(account_id=account.id))
        rows = [self._attach_identity(account.id, identity) for identity in identities]
        session.add_all(rows)
        await session.flush()
        return _with_identities(account, sorted(rows, key=lambda row: (row.provider, row.id)))

    async def get_by_id(self, account_id: int) -> Account | None:
        """Return an account by internal identifier."""
        session = self._session()
        model = await session.get(Account, account_id)
        return None if model is None else await self._load_account(session, model)

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return several accounts, with their identities, in two queries regardless of count.

        Anything presenting a list of accounts — the staff claim queue, for one — needs this
        rather than a `get_by_id` per row.
        """
        session = self._session()
        if not account_ids:
            return {}
        models = (
            await session.scalars(select(Account).where(Account.id.in_(set(account_ids))).order_by(Account.id))
        ).all()
        return await self._load_accounts(session, models)

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        """Return the account holding one canonical provider subject."""
        session = self._session()
        model = await session.scalar(
            select(Account)
            .join(AccountIdentity, AccountIdentity.account_id == Account.id)
            .where(AccountIdentity.provider == provider, AccountIdentity.subject == subject)
        )
        return None if model is None else await self._load_account(session, model)

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        """Resolve one external identity, atomically creating its account when absent.

        `consent` is written only on the row this call creates. An account that already exists is
        returned untouched, because a receipt is evidence that somebody was asked, and this method
        cannot tell whether the caller asked or merely arrived holding a subject.
        """
        identity = AccountIdentity.for_provider(provider, subject)
        session = self._session()
        existing = await self._find_account(session, identity.provider, identity.subject)
        if existing is not None:
            return await self._load_account(session, existing)

        candidate = Account(
            consent_version=None if consent is None else consent.version,
            consented_at=None if consent is None else consent.granted_at,
        )
        session.add(candidate)
        await session.flush()
        # Rides the same transaction as the candidate account: if the identity insert loses
        # the race below, deleting the candidate takes this row with it via CASCADE.
        session.add(AccountProfile(account_id=candidate.id))
        inserted_id = await session.scalar(
            insert(AccountIdentity)
            .values(
                account_id=candidate.id,
                provider=identity.provider,
                subject=identity.subject,
                verified_at=now(),
            )
            .on_conflict_do_nothing(index_elements=[AccountIdentity.provider, AccountIdentity.subject])
            .returning(AccountIdentity.id)
        )
        if inserted_id is None:
            await session.delete(candidate)
            await session.flush()
            holder = await self._find_account(session, identity.provider, identity.subject)
            assert holder is not None
            return await self._load_account(session, holder)
        return await self._load_account(session, candidate)

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account:
        """Replace an account's privacy-notice receipt."""
        session = self._session()
        model = await session.get(Account, account_id, with_for_update=True)
        if model is None:
            raise AccountNotFoundError(account_id)
        model.consent_version = consent.version
        model.consented_at = consent.granted_at
        await session.flush()
        return await self._load_account(session, model)

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None:
        """Remove one owned identity, refusing to remove the account's last sign-in method.

        Addressed by id because a merge can leave multiple identities from the same provider.
        Creator credit is independent of sign-in identities and remains untouched.
        """
        session = self._session()
        # Link and merge paths take this same lock. Keep the delete and remaining-identity
        # check together so concurrent unlinks cannot both remove the last sign-in method.
        account = await session.get(Account, account_id, with_for_update=True)
        if account is None:
            return None
        removed = await session.scalar(
            delete(AccountIdentity)
            .where(
                AccountIdentity.id == identity_id,
                AccountIdentity.account_id == account_id,
            )
            .returning(AccountIdentity)
        )
        if removed is None:
            return None
        remaining = await session.scalar(
            select(func.count()).select_from(AccountIdentity).where(AccountIdentity.account_id == account_id)
        )
        if not remaining:
            raise LastIdentityError(account_id=account_id)
        # ON DELETE SET NULL clears a removed avatar; rejecting the unlink rolls that back too.
        return removed

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one identity on the account's public profile."""
        session = self._session()
        updated = await session.scalar(
            update(AccountIdentity)
            .where(
                AccountIdentity.id == identity_id,
                AccountIdentity.account_id == account_id,
            )
            .values(is_public=is_public)
            .returning(AccountIdentity)
        )
        if updated is None:
            raise AccountIdentityNotFoundError(identity_id, account_id=account_id)
        return updated

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Record the provider rendering key an avatar URL needs.

        Separate from the profile write because it is not a user edit: the bot refreshes it
        opportunistically whenever it happens to hold a fresh `discord.User`.
        """
        session = self._session()
        await session.execute(
            update(AccountIdentity)
            .where(
                AccountIdentity.id == identity_id,
                AccountIdentity.account_id == account_id,
            )
            .values(avatar_key=avatar_key)
        )

    async def get_profile(self, account_id: int) -> AccountProfile | None:
        """Return an account's own profile, visibility flags and all."""
        session = self._session()
        model = await session.get(AccountProfile, account_id)
        # Every account is given a profile row, but a read that predates its creation must
        # render as an empty profile rather than a 500.
        if model is None:
            exists = await session.scalar(select(Account.id).where(Account.id == account_id))
            return None if exists is None else AccountProfile.empty(account_id)
        return model

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile:
        """Apply a validated partial edit, creating the profile row if it is somehow missing."""
        session = self._session()
        # Match unlink and merge lock order so an avatar cannot disappear after its ownership check.
        account = await session.get(Account, account_id, with_for_update=True)
        if account is None:
            raise AccountNotFoundError(account_id)
        model = await session.get(AccountProfile, account_id, with_for_update=True)
        if model is None:
            model = AccountProfile(account_id=account_id)
            session.add(model)
            await session.flush()
        avatar_identity_id = update_request.avatar_identity_id
        # An `int` is the only case that needs checking: UNSET leaves the avatar alone and
        # `None` clears it, and neither can point at somebody else's identity.
        if isinstance(avatar_identity_id, int):
            # The foreign key cannot express "and it must be yours" without a composite key
            # that would break ON DELETE SET NULL, so ownership is checked here.
            owner = await session.scalar(
                select(AccountIdentity.account_id).where(AccountIdentity.id == avatar_identity_id)
            )
            if owner != account_id:
                raise AccountIdentityNotFoundError(avatar_identity_id, account_id=account_id)
        update_request.apply(model)
        model.updated_at = now()
        await session.flush()
        return model

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Reset a profile to the state a new account starts with.

        Staff moderation. Clearing leaves the profile *visible*: `hidden` is the owner's control,
        and a takedown that also hid the page would silently take that decision away from them.
        """
        return await self.upsert_profile(
            account_id,
            ProfileUpdate(display_name=None, bio=None, pronouns=None, links=(), hidden=False, avatar_identity_id=None),
        )

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        """Mint the account's merge ticket, replacing any live one.

        Replace rather than insert: one live ticket per account is what keeps a short code safe,
        and minting a second would double a guesser's target surface for no user-visible gain.
        """
        timestamp = now()
        expires_at = timestamp.add(seconds=ttl_seconds)
        digest = self.hash_verification_code(code)
        session = self._session()
        account = await session.get(Account, account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        await session.execute(
            insert(MergeTicket)
            .values(account_id=account_id, code_digest=digest, created_at=timestamp, expires_at=expires_at)
            .on_conflict_do_update(
                index_elements=[MergeTicket.account_id],
                set_={"code_digest": digest, "created_at": timestamp, "expires_at": expires_at},
            )
        )
        return MergeTicket(account_id=account_id, created_at=timestamp, expires_at=expires_at)

    async def peek_merge_ticket(self, code: str) -> MergeTicket | None:
        """Return a live ticket without spending it, for a confirmation screen."""
        session = self._session()
        row = await session.scalar(
            select(MergeTicket).where(
                MergeTicket.code_digest == self.hash_verification_code(code),
                MergeTicket.expires_at > now(),
            )
        )
        return None if row is None else row

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None:
        """Spend a live ticket, returning it, or `None` when there is nothing to spend.

        The delete is the redemption: a code that reaches here twice fails the second time, so a
        replayed request cannot merge a second account into the survivor.
        """
        session = self._session()
        row = await session.scalar(
            delete(MergeTicket)
            .where(
                MergeTicket.code_digest == self.hash_verification_code(code),
                MergeTicket.expires_at > now(),
            )
            .returning(MergeTicket)
        )
        return None if row is None else row

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge:
        """Move resources to one account and preserve the absorbed public ID as a redirect."""
        session = self._session()
        return await merge_accounts(session, AccountMergeContext(surviving_account_id, absorbed_account_id))

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        """Return a case-insensitive creator alias."""
        session = self._session()
        row = (
            await session.execute(
                select(CreatorAlias, Account.public_creator_id)
                .outerjoin(Account, Account.id == CreatorAlias.account_id)
                .where(CreatorAlias.normalized_name == fold_creator_name(name))
            )
        ).one_or_none()
        return None if row is None else _with_creator(row[0], row[1])

    async def get_creator_profile(self, public_id: uuid.UUID) -> CreatorProfile | None:
        """Return aliases for a public ID, including a permanent merge redirect."""
        session = self._session()
        account = await session.scalar(select(Account).where(Account.public_creator_id == public_id))
        canonical_id = public_id
        if account is None:
            account = await session.scalar(
                select(Account)
                .join(PublicCreatorRedirect, PublicCreatorRedirect.target_account_id == Account.id)
                .where(PublicCreatorRedirect.retired_public_creator_id == public_id)
            )
            if account is None:
                return None
            canonical_id = account.public_creator_id
        aliases = (
            await session.scalars(
                select(CreatorAlias.name)
                .where(CreatorAlias.account_id == account.id)
                .order_by(CreatorAlias.normalized_name)
            )
        ).all()
        return CreatorProfile(public_id=public_id, aliases=tuple(aliases), canonical_public_id=canonical_id)

    async def get_creator_profile_record(self, public_id: uuid.UUID) -> CreatorProfileRecord | None:
        """Return everything known about one creator, before visibility is applied.

        Deliberately unfiltered: `present_public_profile` decides what a stranger sees, and it can
        only be the one authority on that if persistence hands it the whole truth.
        """
        session = self._session()
        account = await session.scalar(select(Account).where(Account.public_creator_id == public_id))
        canonical_id = public_id
        if account is None:
            account = await session.scalar(
                select(Account)
                .join(PublicCreatorRedirect, PublicCreatorRedirect.target_account_id == Account.id)
                .where(PublicCreatorRedirect.retired_public_creator_id == public_id)
            )
            if account is None:
                return None
            canonical_id = account.public_creator_id

        profile_model = await session.get(AccountProfile, account.id)
        profile = AccountProfile.empty(account.id) if profile_model is None else profile_model
        identities = (
            await session.scalars(
                select(AccountIdentity)
                .where(AccountIdentity.account_id == account.id)
                .order_by(AccountIdentity.provider, AccountIdentity.id)
            )
        ).all()
        # One grouped count for every alias at once: a creator page renders every name they
        # hold, and a count per name would be an N+1 on the most public read in the system.
        credit_counts = (
            select(_BUILD_CREDITS.c.alias_id, func.count().label("build_count"))
            .group_by(_BUILD_CREDITS.c.alias_id)
            .subquery()
        )
        alias_rows = (
            await session.execute(
                select(CreatorAlias.name, func.coalesce(credit_counts.c.build_count, 0))
                .outerjoin(credit_counts, credit_counts.c.alias_id == CreatorAlias.id)
                .where(CreatorAlias.account_id == account.id)
                .order_by(CreatorAlias.normalized_name)
            )
        ).all()
        return CreatorProfileRecord(
            public_id=public_id,
            account_id=account.id,
            profile=profile,
            identities=tuple(identity for identity in identities),
            aliases=tuple(CreditedAlias(name=name, build_count=count) for name, count in alias_rows),
            joined_at=account.created_at,
            canonical_public_id=canonical_id,
        )

    async def claim_unclaimed_alias(self, *, account_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Claim an alias only if it remains unclaimed at update time."""
        session = self._session()
        alias = await session.scalar(
            update(CreatorAlias)
            .where(
                CreatorAlias.normalized_name == fold_creator_name(name),
                CreatorAlias.account_id.is_(None),
            )
            .values(account_id=account_id, claimed_at=now(), claim_method=method)
            .returning(CreatorAlias)
        )
        public_id = await session.scalar(select(Account.public_creator_id).where(Account.id == account_id))
        return None if alias is None else _with_creator(alias, public_id)

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim:
        """Open a pending alias claim, reusing an existing matching request."""
        session = self._session()
        alias = await session.scalar(
            select(CreatorAlias).where(CreatorAlias.normalized_name == fold_creator_name(name))
        )
        if alias is None:
            raise CreatorAliasNotFoundError(name)
        if alias.account_id is not None:
            raise AliasAlreadyClaimedError(
                alias.name,
                holder_public_creator_id=await self._public_creator_id(session, alias.account_id),
                holder_account_id=alias.account_id,
            )
        existing = await session.scalar(
            select(AliasClaim).where(
                AliasClaim.alias_id == alias.id,
                AliasClaim.account_id == account_id,
                AliasClaim.status == ClaimStatus.PENDING,
            )
        )
        if existing is not None:
            return _with_claimant(existing, alias.name)
        claim = AliasClaim(alias_id=alias.id, account_id=account_id)
        session.add(claim)
        await session.flush()
        return _with_claimant(claim, alias.name)

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        """Return one claim by identifier."""
        session = self._session()
        row = (
            await session.execute(
                select(AliasClaim, CreatorAlias.name)
                .join(CreatorAlias, CreatorAlias.id == AliasClaim.alias_id)
                .where(AliasClaim.id == claim_id)
            )
        ).one_or_none()
        return None if row is None else _with_claimant(row[0], row[1])

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List claims awaiting staff review, oldest first.

        *with_claimants* loads every claimant's account in the same two extra queries no
        matter how long the queue is, which is what presenting a claimant as anything better
        than an internal account ID needs.
        """
        session = self._session()
        rows = (
            await session.execute(
                select(AliasClaim, CreatorAlias.name)
                .join(CreatorAlias, CreatorAlias.id == AliasClaim.alias_id)
                .where(AliasClaim.status == ClaimStatus.PENDING)
                .order_by(AliasClaim.created_at)
            )
        ).all()
        claimants: dict[int, Account] = {}
        if with_claimants and rows:
            models = (
                await session.scalars(
                    select(Account).where(Account.id.in_({claim.account_id for claim, _ in rows})).order_by(Account.id)
                )
            ).all()
            claimants = await self._load_accounts(session, models)
        return tuple(_with_claimant(claim, name, claimants.get(claim.account_id)) for claim, name in rows)

    async def resolve_claim(
        self,
        *,
        claim_id: int,
        status: ClaimStatus,
        resolved_by_account_id: int,
        reassign: bool = False,
    ) -> AliasClaim:
        """Approve or reject a pending claim.

        Approving a name that is already credited to someone else is refused unless *reassign*
        is set. A rename into a contested name opens exactly such a claim, so staff need a way
        to move the credit — but moving it is a deliberate act, never a side effect of routine
        approval.
        """
        session = self._session()
        claim = await session.get(AliasClaim, claim_id, with_for_update=True)
        if claim is None or claim.status != ClaimStatus.PENDING:
            raise ClaimNotFoundError(claim_id)
        alias = await session.get(CreatorAlias, claim.alias_id, with_for_update=True)
        if alias is None:
            raise ClaimNotFoundError(claim_id)
        if status is ClaimStatus.APPROVED:
            if alias.account_id is not None and not (reassign and alias.account_id != claim.account_id):
                raise AliasAlreadyClaimedError(
                    alias.name,
                    holder_public_creator_id=await self._public_creator_id(session, alias.account_id),
                    holder_account_id=alias.account_id,
                    # Staff audience: the flag that moves a held credit already exists, so say so
                    # rather than telling a reviewer to ask staff.
                    end_user_action=tr(t"Approve with `reassign: True` to move the name to the claimant."),
                )
            alias.account_id = claim.account_id
            alias.claimed_at = now()
            alias.claim_method = ClaimMethod.STAFF_APPROVED
        claim.status = status
        claim.resolved_at = now()
        claim.resolved_by_account_id = resolved_by_account_id
        await session.flush()
        # The claimant comes back with the claim so a resolution can name who was credited
        # instead of saying "the claimant"; one account, so `_load_accounts` costs two queries.
        claimant = await session.get(Account, claim.account_id)
        loaded = None if claimant is None else (await self._load_accounts(session, [claimant])).get(claim.account_id)
        return _with_claimant(claim, alias.name, loaded)

    def hash_verification_code(self, code: str) -> str:
        """Return the digest stored for a short-lived verification code.

        Keyed, not prefixed. The pepper is a key, and `sha256(pepper || code)` is the weaker
        construction for no saving. Unlike every other credential in this codebase the input here is
        human-sized, so see `docs/credential-hashing.md` for why the answer is still a keyed digest
        rather than a KDF: the code is short-lived and attempt-capped, which is what buys the safety
        margin a work factor would otherwise have to.

        No dual-read path and no backfill. Codes expire in ten minutes, so a deploy invalidates at
        most one window and the in-game `/link` reissues.
        """
        # codeql[py/weak-sensitive-data-hashing]
        return hmac.digest(self._verification_code_pepper.encode(), code.encode(), hashlib.sha256).hex()

    @staticmethod
    async def _public_creator_id(session: AsyncSession, account_id: int) -> uuid.UUID | None:
        """Resolve an account's public creator identity, for naming a conflict safely."""
        return await session.scalar(select(Account.public_creator_id).where(Account.id == account_id))

    def _reservation_holds(self, row: VerificationCodeModel, token: str) -> bool:
        """Whether *token* is the live hold on *row*."""
        if row.reserved_token is None or row.reserved_until is None:
            return False
        return hmac.compare_digest(row.reserved_token, self.hash_verification_code(token)) and (
            row.reserved_until > Instant.now()
        )

    async def reserve_verification_code(self, code: str, *, ttl_seconds: int) -> LinkReservation | None:
        """Hold a valid code for *ttl_seconds* and describe what redeeming it would do.

        Returns `None` for a code that is unknown, expired, spent, or already held by someone else's
        live prompt. Nothing about the caller is written: the hold is a token digest and a deadline
        on a row that already existed, so a prompt that is cancelled leaves no trace of whoever saw
        it.
        """
        timestamp = now()
        session = self._session()
        row = await session.scalar(
            select(VerificationCodeModel)
            .where(
                VerificationCodeModel.expires > timestamp,
                VerificationCodeModel.code == self.hash_verification_code(code),
                VerificationCodeModel.valid.is_(True),
                or_(
                    VerificationCodeModel.reserved_until.is_(None),
                    VerificationCodeModel.reserved_until <= timestamp,
                ),
            )
            .with_for_update()
        )
        if row is None:
            return None

        token = secrets.token_urlsafe(32)
        expires_at = timestamp.add(seconds=ttl_seconds)
        row.reserved_token = self.hash_verification_code(token)
        row.reserved_until = expires_at
        preview = await self._preview_link(session, row)
        return LinkReservation(token=token, expires_at=expires_at, preview=preview)

    async def _preview_link(self, session: AsyncSession, row: VerificationCodeModel) -> LinkPreview:
        """Describe the effects of redeeming *row* without redeeming it."""
        java_subject = str(row.minecraft_uuid)
        held_elsewhere = await session.scalar(
            select(AccountIdentity.id).where(
                AccountIdentity.provider == IdentityProvider.JAVA,
                AccountIdentity.subject == java_subject,
            )
        )
        alias_row = (
            await session.execute(
                select(CreatorAlias, Account.public_creator_id)
                .outerjoin(Account, CreatorAlias.account_id == Account.id)
                .where(CreatorAlias.normalized_name == fold_creator_name(row.username))
            )
        ).first()

        credit = None
        if alias_row is not None:
            alias, public_creator_id = alias_row
            build_count = await session.scalar(
                select(func.count()).select_from(_BUILD_CREDITS).where(_BUILD_CREDITS.c.alias_id == alias.id)
            )
            credit = CreditPreview(
                name=alias.name,
                build_count=build_count or 0,
                # Only a *held* alias reports an owner; an unclaimed one has no account to join to.
                held_by_public_creator_id=public_creator_id if alias.account_id is not None else None,
            )
        return LinkPreview(
            java_uuid=row.minecraft_uuid,
            username=row.username,
            credit=credit,
            java_uuid_held_elsewhere=held_elsewhere is not None,
        )

    async def release_verification_code(self, code: str, reservation_token: str) -> bool:
        """Drop a hold so the code is usable again at once rather than after the deadline.

        Idempotent, and safe to call for a hold that already lapsed or was taken over, because it
        only clears a row whose live token matches.
        """
        session = self._session()
        row = await session.scalar(
            select(VerificationCodeModel)
            .where(VerificationCodeModel.code == self.hash_verification_code(code))
            .with_for_update()
        )
        if row is None or not self._reservation_holds(row, reservation_token):
            return False
        row.reserved_token = None
        row.reserved_until = None
        return True

    async def verification_lockout(self, provider: IdentityProvider, subject: str) -> Instant | None:
        """Return when an identity's redemption lockout ends, or `None` when it may try."""
        session = self._session()
        locked_until = await session.scalar(
            select(VerificationAttemptModel.locked_until).where(
                VerificationAttemptModel.provider == provider,
                VerificationAttemptModel.subject == subject,
            )
        )
        return locked_until if locked_until is not None and locked_until > Instant.now() else None

    async def record_verification_failure(
        self, provider: IdentityProvider, subject: str, *, max_failures: int, lockout_seconds: int
    ) -> Instant | None:
        """Count one refused code and return the lockout instant when this failure caused one.

        The increment is a single upsert rather than a read-modify-write, so concurrent guesses
        cannot each read the same count and overwrite one another — which would have made the cap
        trivially evadable by sending attempts in parallel.
        """
        timestamp = now()
        locked_until = timestamp.add(seconds=lockout_seconds)
        # Reaching the cap resets the count as it starts the cooling-off period, so the window after
        # a lockout is a fresh budget rather than an instant re-lock on the next single failure.
        reached_cap = VerificationAttemptModel.consecutive_failures + 1 >= max_failures
        first_failure_caps = max_failures <= 1
        # Typed explicitly: inside `case()` the bind is not associated with the column, so it loses
        # the `InstantUTC` adapter and reaches asyncpg as a bare `Instant` it cannot encode.
        lockout_bind = literal(locked_until, InstantUTC())
        statement = (
            insert(VerificationAttemptModel)
            .values(
                provider=provider,
                subject=subject,
                consecutive_failures=0 if first_failure_caps else 1,
                locked_until=lockout_bind if first_failure_caps else None,
                updated_at=timestamp,
            )
            .on_conflict_do_update(
                index_elements=[VerificationAttemptModel.provider, VerificationAttemptModel.subject],
                set_={
                    "consecutive_failures": case(
                        (reached_cap, 0), else_=VerificationAttemptModel.consecutive_failures + 1
                    ),
                    "locked_until": case((reached_cap, lockout_bind), else_=VerificationAttemptModel.locked_until),
                    "updated_at": timestamp,
                },
            )
            .returning(VerificationAttemptModel.locked_until)
        )
        session = self._session()
        stored_lockout = await session.scalar(statement)
        # Only report a lockout *this* call started. Comparing for equality rather than for being in
        # the future distinguishes "I caused this" from "one was already running", which matters both
        # for the caller's log line and for a race: two attempts crossing the cap together compute
        # instants a microsecond apart, so exactly one sees its own value survive. Exact comparison is
        # sound because `now()` floors to the precision `timestamptz` keeps.
        return locked_until if stored_lockout == locked_until else None

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None:
        """Forget an identity's failures after a successful redemption."""
        session = self._session()
        await session.execute(
            delete(VerificationAttemptModel).where(
                VerificationAttemptModel.provider == provider,
                VerificationAttemptModel.subject == subject,
            )
        )

    async def consume_code_and_link_account(
        self,
        *,
        account_id: int,
        code: str,
        consent: AccountConsent,
        reservation_token: str | None = None,
    ) -> VerificationLinkResult:
        """Consume one code and attach its Java identity to *account_id* atomically.

        The account must already exist. Redeeming a code is evidence of a *Java* subject and
        of nothing else, so this path no longer mints an identity in any other namespace;
        whoever holds evidence of the caller's own provider creates the account before
        calling. That is what lets a Minecraft-only or CLI-only caller link at all.

        *reservation_token* commits a hold taken by `reserve_verification_code`. The hold is checked
        after the code is found rather than as part of finding it, so a lapsed prompt can be reported
        as itself instead of as an invalid code. Everything else is still re-checked under the lock:
        the reservation freezes the code, not the world, so the creator credit a preview described
        may have been claimed in the meantime.
        """
        session = self._session()
        verification_code = await session.scalar(
            select(VerificationCodeModel)
            .where(
                VerificationCodeModel.expires > Instant.now(),
                VerificationCodeModel.code == self.hash_verification_code(code),
                VerificationCodeModel.valid.is_(True),
            )
            .with_for_update()
        )
        if verification_code is None:
            return VerificationLinkResult()
        if reservation_token is not None and not self._reservation_holds(verification_code, reservation_token):
            return VerificationLinkResult(reservation_expired=True)

        account = await session.get(Account, account_id, with_for_update=True)
        if account is None:
            raise AccountNotFoundError(account_id)
        java_subject = str(verification_code.minecraft_uuid)
        java_holder = await self._find_account(session, IdentityProvider.JAVA, java_subject, for_update=True)
        existing_java = await session.scalar(
            select(AccountIdentity).where(
                AccountIdentity.account_id == account.id,
                AccountIdentity.provider == IdentityProvider.JAVA,
            )
        )
        if existing_java is not None and existing_java.subject != java_subject:
            return VerificationLinkResult(
                account=await self._load_account(session, account),
                conflicting_java_uuid=uuid.UUID(existing_java.subject),
            )
        if java_holder is not None and java_holder.id != account.id:
            return VerificationLinkResult(
                account=await self._load_account(session, account),
                conflicting_java_uuid=verification_code.minecraft_uuid,
            )

        previous_name = None
        if existing_java is None:
            session.add(
                self._attach_identity(
                    account.id,
                    AccountIdentity.java(
                        verification_code.minecraft_uuid,
                        username=verification_code.username,
                        verified_at=now(),
                    ),
                )
            )
        else:
            previous_name = existing_java.display_name
            existing_java.display_name = verification_code.username
            existing_java.verified_at = now()
        account.consent_version = consent.version
        account.consented_at = consent.granted_at
        verification_code.valid = False

        refresh = await self._reconcile_java_name(
            session,
            account=account,
            java_uuid=verification_code.minecraft_uuid,
            username=verification_code.username,
            previous_name=previous_name,
        )
        await session.flush()
        return VerificationLinkResult(
            account=await self._load_account(session, account),
            claimed_alias=refresh.claimed_alias,
            refresh=refresh,
        )

    async def refresh_java_identity(
        self,
        *,
        account_id: int,
        java_uuid: uuid.UUID,
        username: str,
    ) -> IdentityRefresh:
        """Record a freshly observed Java name and reconcile the creator credit that follows it.

        Runs the same reconciliation the link path does, so a rename is handled identically
        whether it is noticed during linking or by an explicit refresh.
        """
        session = self._session()
        account = await session.get(Account, account_id, with_for_update=True)
        if account is None:
            raise AccountNotFoundError(account_id)
        identity = await session.scalar(
            select(AccountIdentity)
            .where(
                AccountIdentity.account_id == account_id,
                AccountIdentity.provider == IdentityProvider.JAVA,
                AccountIdentity.subject == str(java_uuid),
            )
            .with_for_update()
        )
        if identity is None:
            raise MinecraftAccountNotFoundError(java_uuid)
        previous_name = identity.display_name
        identity.display_name = username
        identity.verified_at = now()
        return await self._reconcile_java_name(
            session,
            account=account,
            java_uuid=java_uuid,
            username=username,
            previous_name=previous_name,
        )

    @staticmethod
    async def _reconcile_java_name(
        session: AsyncSession,
        *,
        account: Account,
        java_uuid: uuid.UUID,
        username: str,
        previous_name: str | None,
    ) -> IdentityRefresh:
        """Attach the verified name's creator credit, without ever taking one from someone else.

        The four cases are exhaustive by design, so a rename can never fall through into
        "nothing happened": the name is unknown, free, already ours, or someone else's. Only
        the last needs a human, and it gets a pending claim rather than a silent no-op.

        Aliases claimed under previous names are deliberately left attached. A rename does not
        retract the credit for work published under the old name.
        """
        folded = fold_creator_name(username)
        claimed: CreatorAlias | None = None
        contested: CreatorAlias | None = None
        opened_claim: AliasClaim | None = None

        alias = await session.scalar(
            select(CreatorAlias).where(CreatorAlias.normalized_name == folded).with_for_update()
        )
        if alias is None:
            # No build has credited this name yet. Create it so the public profile shows the
            # current name rather than lagging until someone happens to credit it.
            inserted = await session.scalar(
                insert(CreatorAlias)
                .values(
                    name=username,
                    account_id=account.id,
                    claimed_at=now(),
                    claim_method=ClaimMethod.VERIFIED_IGN,
                )
                .on_conflict_do_nothing(index_elements=[CreatorAlias.normalized_name])
                .returning(CreatorAlias)
            )
            alias = inserted or await session.scalar(
                select(CreatorAlias).where(CreatorAlias.normalized_name == folded).with_for_update()
            )
            assert alias is not None
        if alias.account_id is None:
            alias.account_id = account.id
            alias.claimed_at = now()
            alias.claim_method = ClaimMethod.VERIFIED_IGN
            claimed = _with_creator(alias, account.public_creator_id)
        elif alias.account_id == account.id:
            claimed = _with_creator(alias, account.public_creator_id)
        else:
            # Someone else is credited under this name. Never transfer on a rename; open a
            # claim so it lands in the staff queue instead of vanishing.
            contested = _with_creator(alias)
            claim = await session.scalar(
                insert(AliasClaim)
                .values(alias_id=alias.id, account_id=account.id, status=ClaimStatus.PENDING)
                # `creator_alias_claims_one_pending_per_account` is partial, so the predicate has
                # to be restated here or Postgres cannot match the conflict to it.
                .on_conflict_do_nothing(
                    index_elements=[AliasClaim.alias_id, AliasClaim.account_id],
                    index_where=text("status = 'pending'"),
                )
                .returning(AliasClaim)
            )
            claim = claim or await session.scalar(
                select(AliasClaim).where(
                    AliasClaim.alias_id == alias.id,
                    AliasClaim.account_id == account.id,
                    AliasClaim.status == ClaimStatus.PENDING,
                )
            )
            opened_claim = None if claim is None else _with_claimant(claim, alias.name)

        await session.flush()
        retained = (
            await session.scalars(
                select(CreatorAlias.name)
                .where(CreatorAlias.account_id == account.id, CreatorAlias.normalized_name != folded)
                .order_by(CreatorAlias.normalized_name)
            )
        ).all()
        return IdentityRefresh(
            account_id=account.id,
            java_uuid=java_uuid,
            current_name=username,
            previous_name=previous_name,
            claimed_alias=claimed,
            retained_alias_names=tuple(retained),
            contested_alias=contested,
            opened_claim=opened_claim,
        )

    async def replace_verification_code(
        self,
        *,
        minecraft_uuid: uuid.UUID,
        code: str,
        username: str,
    ) -> None:
        """Invalidate prior codes and insert their replacement atomically."""
        session = self._session()
        await session.execute(
            update(VerificationCodeModel)
            .where(
                VerificationCodeModel.minecraft_uuid == minecraft_uuid,
                VerificationCodeModel.valid.is_(True),
            )
            .values(valid=False)
        )
        session.add(
            VerificationCodeModel(
                minecraft_uuid=minecraft_uuid,
                code=self.hash_verification_code(code),
                username=username,
            )
        )

    @staticmethod
    def _attach_identity(account_id: int, identity: AccountIdentity) -> AccountIdentity:
        identity.account_id = account_id
        identity.verified_at = identity.verified_at or now()
        return identity

    @staticmethod
    async def _find_account(
        session: AsyncSession,
        provider: IdentityProvider,
        subject: str,
        *,
        for_update: bool = False,
    ) -> Account | None:
        statement = (
            select(Account)
            .join(AccountIdentity, AccountIdentity.account_id == Account.id)
            .where(AccountIdentity.provider == provider, AccountIdentity.subject == subject)
        )
        if for_update:
            statement = statement.with_for_update(of=Account)
        return await session.scalar(statement)

    @classmethod
    async def _load_account(cls, session: AsyncSession, account: Account) -> Account:
        loaded = await cls._load_accounts(session, [account])
        return loaded[account.id]

    @staticmethod
    async def _load_accounts(session: AsyncSession, accounts: Sequence[Account]) -> dict[int, Account]:
        """Load identities for several accounts in one query.

        Fetch all identities together so loading a list of accounts takes two queries.
        """
        if not accounts:
            return {}
        account_ids = [account.id for account in accounts]
        identities = (
            await session.scalars(
                select(AccountIdentity)
                .where(AccountIdentity.account_id.in_(account_ids))
                .order_by(AccountIdentity.provider, AccountIdentity.id)
            )
        ).all()
        grouped: dict[int, list[AccountIdentity]] = {account_id: [] for account_id in account_ids}
        for identity in identities:
            grouped[identity.account_id].append(identity)
        return {account.id: _with_identities(account, grouped[account.id]) for account in accounts}
