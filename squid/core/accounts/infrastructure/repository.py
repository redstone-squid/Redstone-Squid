"""SQLAlchemy account repository, keyed by account rather than by identity provider."""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Collection, Sequence

from sqlalchemy import ColumnElement, case, delete, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.accounts.application.ports import VerificationLinkResult
from squid.core.accounts.domain import (
    AccountMerge,
    ClaimStatus,
    CreatorProfile,
    CreatorProfileRecord,
    CreditedAlias,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    PrivacyNoticeReceipt,
    ProfileUpdate,
    fold_creator_name,
)
from squid.core.accounts.errors import (
    AccountIdentityNotFoundError,
    AccountNotFoundError,
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    CreatorAliasNotFoundError,
    LastIdentityError,
    MinecraftAccountNotFoundError,
)
from squid.core.accounts.infrastructure.account_merge import merge_accounts
from squid.core.accounts.infrastructure.attribution import attributed_credits
from squid.core.accounts.models import (
    Account,
    AccountIdentity,
    AccountProfile,
    AliasClaim,
    CataloguePreference,
    ClaimedCredit,
    CreatorAlias,
    MergeTicket,
    ProfileClearAudit,
    PublicAccountRedirect,
)
from squid.core.accounts.models import VerificationAttempt as VerificationAttemptModel
from squid.core.accounts.models import VerificationCode as VerificationCodeModel
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, BuildCredit
from squid.core.edits.domain import Revision
from squid.core.errors import ConflictError, ValidationError
from squid.core.persistence.account_merge import AccountMergeContext
from squid.core.persistence.revisions import flush_revision
from squid.core.persistence.types import InstantUTC


def _hydrate_account(model: Account, identities: Sequence[AccountIdentity]) -> Account:
    """Populate identities and the consent receipt on the same loaded account."""
    model.identities = tuple(identities)
    model.consent = None
    if model.privacy_notice_version is not None and model.privacy_notice_acknowledged_at is not None:
        model.consent = PrivacyNoticeReceipt(
            version=model.privacy_notice_version, granted_at=model.privacy_notice_acknowledged_at
        )
    return model


def _hydrate_claim(claim: AliasClaim, alias_name: str, claimant: Account | None = None) -> AliasClaim:
    """Populate the alias name and optional claimant on the same loaded claim."""
    claim.alias_name = alias_name
    claim.claimant = claimant
    return claim


class AccountRepository:
    """Store accounts, external identities, creator credits, and verification codes.

    Methods share the caller's transaction and do not commit. Callers must roll back
    after an error: for example, unlink_identity deletes before checking whether it
    removed the last identity, and relies on rollback when that check fails.
    """

    def __init__(self, session: Callable[[], AsyncSession], verification_code_pepper: str):
        self._session = session
        self._verification_code_pepper = verification_code_pepper

    # Account lookup and consent.

    async def account_exists(self, account_id: int) -> bool:
        return await self._session().scalar(select(Account.id).where(Account.id == account_id)) is not None

    async def can_approve_minecraft_identity(self, *, account_id: int, java_uuid: uuid.UUID) -> bool:
        """Check ownership of the verified Java UUID in one query."""
        session = self._session()
        result = await session.scalar(
            select(AccountIdentity.id)
            .join(Account, Account.id == AccountIdentity.account_id)
            .where(
                Account.id == account_id,
                AccountIdentity.provider == IdentityProvider.JAVA,
                AccountIdentity.subject == str(java_uuid),
                AccountIdentity.verified_at.is_not(None),
            )
        )
        return result is not None

    async def create(
        self,
        *,
        consent: PrivacyNoticeReceipt | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        """Insert one account and its verified identities atomically."""
        session = self._session()
        account = Account(
            privacy_notice_version=None if consent is None else consent.version,
            privacy_notice_acknowledged_at=None if consent is None else consent.granted_at,
        )
        session.add(account)
        await session.flush()
        session.add(AccountProfile(account_id=account.id))
        rows = [self._attach_identity(account.id, identity) for identity in identities]
        session.add_all(rows)
        await session.flush()
        return _hydrate_account(account, sorted(rows, key=lambda row: (row.provider, row.id)))

    async def lock_for_edit(self, account_id: int) -> Account:
        session = self._session()
        account = await session.get(Account, account_id, with_for_update=True)
        if account is None:
            raise AccountNotFoundError(account_id)
        return await self._load_account(session, account)

    async def flush_edit_revision(self, account: Account, expected: Revision) -> Revision:
        return await flush_revision(self._session(), account, expected)

    async def get_by_id(self, account_id: int) -> Account | None:
        """Return an account by internal identifier."""
        session = self._session()
        model = await session.get(Account, account_id)
        return None if model is None else await self._load_account(session, model)

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return known accounts and their identities in at most two queries."""
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

    async def link_browser_identity(self, account_id: int, provider: IdentityProvider, subject: str) -> bool:
        """Attach a verified identity unless another account owns it."""
        session = self._session()
        account = await self.lock_for_edit(account_id)
        identity = AccountIdentity.for_provider(provider, subject)
        inserted = await session.scalar(
            insert(AccountIdentity)
            .values(account_id=account_id, provider=provider, subject=identity.subject, verified_at=Instant.now())
            .on_conflict_do_nothing(index_elements=[AccountIdentity.provider, AccountIdentity.subject])
            .returning(AccountIdentity.id)
        )
        if inserted is not None:
            account.revision += 1
            return True
        holder = await self._find_account(session, provider, identity.subject)
        return holder is not None and holder.id == account_id

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: PrivacyNoticeReceipt | None = None
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
            privacy_notice_version=None if consent is None else consent.version,
            privacy_notice_acknowledged_at=None if consent is None else consent.granted_at,
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
                verified_at=Instant.now(),
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

    async def update_consent(self, account_id: int, consent: PrivacyNoticeReceipt) -> Account:
        """Replace an account's privacy-notice receipt."""
        session = self._session()
        model = await session.get(Account, account_id, with_for_update=True)
        if model is None:
            raise AccountNotFoundError(account_id)
        model.privacy_notice_version = consent.version
        model.privacy_notice_acknowledged_at = consent.granted_at
        await session.flush()
        return await self._load_account(session, model)

    # Linked identities and the profile fields that may refer to them.

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
        account.revision += 1
        # ON DELETE SET NULL clears a removed avatar; rejecting the unlink rolls that back too.
        return removed

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one identity on the account's public profile."""
        session = self._session()
        account = await self.lock_for_edit(account_id)
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
        account.revision += 1
        return updated

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Record the provider rendering key an avatar URL needs.

        Separate from the profile write because it is not a user edit: the bot refreshes it
        opportunistically whenever it happens to hold a fresh `discord.User`.
        """
        session = self._session()
        account = await self.lock_for_edit(account_id)
        changed = await session.scalar(
            update(AccountIdentity)
            .where(
                AccountIdentity.id == identity_id,
                AccountIdentity.account_id == account_id,
                AccountIdentity.avatar_key.is_distinct_from(avatar_key),
            )
            .values(avatar_key=avatar_key)
            .returning(AccountIdentity.id)
        )

        if changed is not None:
            account.revision += 1

    async def catalogue_preferences(self, account_id: int) -> Sequence[CataloguePreference]:
        rows = await self._session().scalars(
            select(CataloguePreference).where(CataloguePreference.account_id == account_id)
        )
        return rows.all()

    async def save_catalogue_preference(self, preference: CataloguePreference) -> None:
        if not preference.filters and not preference.sort:
            await self._session().execute(
                delete(CataloguePreference).where(
                    CataloguePreference.account_id == preference.account_id,
                    CataloguePreference.scope == preference.scope,
                )
            )
            return
        statement = insert(CataloguePreference).values(
            account_id=preference.account_id, scope=preference.scope, filters=preference.filters, sort=preference.sort
        )
        await self._session().execute(
            statement.on_conflict_do_update(
                index_elements=[CataloguePreference.account_id, CataloguePreference.scope],
                set_={"filters": statement.excluded.filters, "sort": statement.excluded.sort},
            )
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
        was_hidden = model.hidden
        update_request.apply(model)
        model.updated_at = Instant.now()
        account.revision += 1
        await session.flush()
        if was_hidden != model.hidden:
            await session.execute(
                text("""SELECT public.upsert_search_projection_request('build', target.build_id::text, 'upsert')
                FROM (SELECT DISTINCT build_id FROM public.build_credit_accounts() WHERE account_id = :account_id ORDER BY build_id) target"""),
                {"account_id": account_id},
            )
        return model

    async def record_profile_clear(
        self, account_id: int, actor_account_id: int, operation_id: uuid.UUID, revision: int
    ) -> None:
        self._session().add(
            ProfileClearAudit(
                operation_id=operation_id,
                account_id=account_id,
                actor_account_id=actor_account_id,
                revision=revision,
            )
        )

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Clear profile content and avatar selection, resetting hidden to False."""
        return await self.upsert_profile(
            account_id,
            ProfileUpdate(display_name=None, bio=None, pronouns=None, links=(), hidden=False, avatar_identity_id=None),
        )

    # Merge tickets authorize moving one existing account into another.

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        """Replace the account's merge ticket so only the newest code remains usable."""
        timestamp = Instant.now()
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
                MergeTicket.expires_at > Instant.now(),
            )
        )
        return None if row is None else row

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None:
        """Delete and return an unexpired ticket, or return None if none matches.

        The caller must commit to consume the ticket. Rolling back restores it.
        """
        session = self._session()
        row = await session.scalar(
            delete(MergeTicket)
            .where(
                MergeTicket.code_digest == self.hash_verification_code(code),
                MergeTicket.expires_at > Instant.now(),
            )
            .returning(MergeTicket)
        )
        return None if row is None else row

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge:
        """Move resources to one account and preserve the absorbed public ID as a redirect."""
        session = self._session()
        return await merge_accounts(session, AccountMergeContext(surviving_account_id, absorbed_account_id))

    # Creator credit and staff review of alias claims.

    async def get_alias(self, alias_id: int) -> CreatorAlias | None:
        """Find an alias by its ID, with the public ID of the account holding it."""
        return await self._hydrated_alias(CreatorAlias.id == alias_id)

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        """Find an alias using the same Unicode normalization and casefold as stored names."""
        return await self._hydrated_alias(CreatorAlias.normalized_name == fold_creator_name(name))

    async def _hydrated_alias(self, condition: ColumnElement[bool]) -> CreatorAlias | None:
        return await self._session().scalar(
            select(CreatorAlias).where(
                condition,
                select(BuildCredit.id)
                .join(Build, Build.id == BuildCredit.build_id)
                .where(BuildCredit.alias_id == CreatorAlias.id, Build.submission_status == BuildStatus.APPROVED)
                .exists(),
            )
        )

    async def account_id_for_public_id(self, public_id: uuid.UUID) -> int | None:
        """Return the account a public ID names, following merge redirects."""
        account = await self._account_for_public_id(self._session(), public_id)
        return None if account is None else account.id

    async def public_ids_for(self, account_ids: Collection[int]) -> dict[int, uuid.UUID]:
        if not account_ids:
            return {}
        rows = await self._session().execute(
            select(Account.id, Account.public_account_id).where(Account.id.in_(account_ids))
        )
        return {account_id: public_id for account_id, public_id in rows.tuples()}

    @staticmethod
    async def _account_for_public_id(session: AsyncSession, public_id: uuid.UUID) -> Account | None:
        account = await session.scalar(select(Account).where(Account.public_account_id == public_id))
        if account is not None:
            return account
        return await session.scalar(
            select(Account)
            .join(PublicAccountRedirect, PublicAccountRedirect.target_account_id == Account.id)
            .where(PublicAccountRedirect.retired_public_account_id == public_id)
        )

    async def get_creator_profile(self, public_id: uuid.UUID) -> CreatorProfile | None:
        """Return aliases for a public ID, including a permanent merge redirect."""
        session = self._session()
        account = await self._account_for_public_id(session, public_id)
        if account is None:
            return None
        canonical_id = account.public_account_id
        attributed = attributed_credits()
        aliases = (
            await session.scalars(
                select(attributed.c.name)
                .distinct()
                .join(Build, Build.id == attributed.c.build_id)
                .where(attributed.c.account_id == account.id, Build.submission_status == BuildStatus.APPROVED)
                .order_by(attributed.c.name)
            )
        ).all()
        return CreatorProfile(public_id=public_id, aliases=tuple(aliases), canonical_public_id=canonical_id)

    async def get_creator_profile_record(self, public_id: uuid.UUID) -> CreatorProfileRecord | None:
        """Load a creator's profile, identities, and credits, following merge redirects.

        Visibility has not been applied. Use PublicCreatorProfile.from_record before exposing the
        result publicly.
        """
        session = self._session()
        account = await self._account_for_public_id(session, public_id)
        if account is None:
            return None
        canonical_id = account.public_account_id

        profile_model = await session.get(AccountProfile, account.id)
        profile = AccountProfile.empty(account.id) if profile_model is None else profile_model
        identities = (
            await session.scalars(
                select(AccountIdentity)
                .where(AccountIdentity.account_id == account.id)
                .order_by(AccountIdentity.provider, AccountIdentity.id)
            )
        ).all()
        attributed = attributed_credits()
        attributed_rows = (
            await session.execute(
                select(attributed.c.name, func.count(func.distinct(attributed.c.build_id)))
                .join(Build, Build.id == attributed.c.build_id)
                .where(attributed.c.account_id == account.id, Build.submission_status == BuildStatus.APPROVED)
                .group_by(attributed.c.name)
            )
        ).all()
        counts = {name: count for name, count in attributed_rows}
        return CreatorProfileRecord(
            public_id=public_id,
            account_id=account.id,
            profile=profile,
            identities=tuple(identity for identity in identities),
            aliases=tuple(CreditedAlias(name=name, build_count=count) for name, count in sorted(counts.items())),
            joined_at=account.created_at,
            canonical_public_id=canonical_id,
        )

    async def credit_owners(self, build_id: int) -> dict[int, uuid.UUID]:
        """Map credit positions to public creator IDs, using the shared attribution rule."""
        attributed = attributed_credits()
        rows = await self._session().execute(
            select(attributed.c.position, Account.public_account_id)
            .join(Account, Account.id == attributed.c.account_id)
            .where(attributed.c.build_id == build_id)
        )
        return {position: public_id for position, public_id in rows}

    async def request_claim(
        self, *, name: str, account_id: int, credit_ids: Collection[uuid.UUID] | None = None
    ) -> AliasClaim:
        """Snapshot the published credits being claimed. Later builds never join a request."""
        session = self._session()
        alias = await session.scalar(
            select(CreatorAlias).where(CreatorAlias.normalized_name == fold_creator_name(name)).with_for_update()
        )
        if alias is None:
            raise CreatorAliasNotFoundError(name)
        owners = func.build_credit_accounts().table_valued("build_id", "position", "account_id")
        query = (
            select(BuildCredit.id, BuildCredit.build_id, owners.c.account_id)
            .join(Build, Build.id == BuildCredit.build_id)
            .outerjoin(
                owners, (owners.c.build_id == BuildCredit.build_id) & (owners.c.position == BuildCredit.position)
            )
            .where(BuildCredit.alias_id == alias.id, Build.submission_status == BuildStatus.APPROVED)
        )
        if credit_ids is not None:
            query = query.where(BuildCredit.id.in_(credit_ids))
        selected = {
            str(row.id): ClaimedCredit(build_id=row.build_id, account_id=row.account_id)
            for row in await session.execute(query)
        }
        if not selected or (credit_ids is not None and set(map(str, credit_ids)) != selected.keys()):
            msg = "Select published credits under this name."
            raise ValidationError(msg)
        existing = await session.scalar(
            select(AliasClaim).where(
                AliasClaim.alias_id == alias.id,
                AliasClaim.account_id == account_id,
                AliasClaim.status == ClaimStatus.PENDING,
                AliasClaim.selected_credits == selected,
            )
        )
        if existing is not None:
            return _hydrate_claim(existing, alias.name)
        claim = AliasClaim(alias_id=alias.id, account_id=account_id, selected_credits=selected)
        session.add(claim)
        await session.flush()
        return _hydrate_claim(claim, alias.name)

    async def claims_for_account(self, account_id: int) -> Sequence[AliasClaim]:
        rows = await self._session().execute(
            select(AliasClaim, CreatorAlias.name)
            .join(CreatorAlias, CreatorAlias.id == AliasClaim.alias_id)
            .where(AliasClaim.account_id == account_id)
            .order_by(AliasClaim.created_at.desc(), AliasClaim.id.desc())
        )
        return tuple(_hydrate_claim(claim, name) for claim, name in rows)

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
        return None if row is None else _hydrate_claim(row[0], row[1])

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List pending claims oldest first.

        With with_claimants=True, load the accounts and their identities in at most two
        extra queries, rather than querying separately for each claim.
        """
        session = self._session()
        rows = (
            await session.execute(
                select(AliasClaim, CreatorAlias.name)
                .join(CreatorAlias, CreatorAlias.id == AliasClaim.alias_id)
                .where(AliasClaim.status == ClaimStatus.PENDING)
                .order_by(AliasClaim.created_at, AliasClaim.id)
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
        return tuple(_hydrate_claim(claim, name, claimants.get(claim.account_id)) for claim, name in rows)

    async def resolve_claim(
        self,
        *,
        claim_id: int,
        status: ClaimStatus,
        resolved_by_account_id: int,
        reassign: bool = False,
        credit_ids: Collection[uuid.UUID] | None = None,
    ) -> AliasClaim:
        """Approve or reject a pending claim.

        Reassignment requires explicit confirmation. If another reviewer changed a
        selected credit after filing, the whole decision fails.
        """
        session = self._session()
        claim = await session.get(AliasClaim, claim_id, with_for_update=True)
        if claim is None or claim.status != ClaimStatus.PENDING:
            raise ClaimNotFoundError(claim_id)
        alias = await session.get(CreatorAlias, claim.alias_id, with_for_update=True)
        if alias is None:
            raise ClaimNotFoundError(claim_id)
        if status is ClaimStatus.APPROVED:
            ids = set(credit_ids) if credit_ids is not None else {uuid.UUID(key) for key in claim.selected_credits}
            if not ids or (claim.selected_credits and not all(str(key) in claim.selected_credits for key in ids)):
                msg = "Select the individual credits to approve."
                raise ValidationError(msg)
            credits = (
                await session.scalars(
                    select(BuildCredit)
                    .join(Build, Build.id == BuildCredit.build_id)
                    .where(
                        BuildCredit.id.in_(ids),
                        BuildCredit.alias_id == claim.alias_id,
                        Build.submission_status == BuildStatus.APPROVED,
                    )
                    .order_by(BuildCredit.build_id, BuildCredit.id)
                    .with_for_update()
                )
            ).all()
            owners = func.build_credit_accounts().table_valued("build_id", "position", "account_id")
            current = dict(
                (
                    await session.execute(
                        select(BuildCredit.id, owners.c.account_id)
                        .select_from(BuildCredit)
                        .outerjoin(
                            owners,
                            (owners.c.build_id == BuildCredit.build_id) & (owners.c.position == BuildCredit.position),
                        )
                        .where(BuildCredit.id.in_(ids))
                    )
                )
                .tuples()
                .all()
            )
            if len(credits) != len(ids) or (
                claim.selected_credits
                and any(current[key] != claim.selected_credits[str(key)]["account_id"] for key in ids)
            ):
                msg = "The selected credits changed after filing. Review a new request."
                raise ConflictError(msg)
            if not claim.selected_credits:
                claim.selected_credits = {
                    str(credit.id): ClaimedCredit(build_id=credit.build_id, account_id=current[credit.id])
                    for credit in credits
                }
            for credit in credits:
                owner = current[credit.id]
                if owner is not None and owner != claim.account_id and not reassign:
                    raise AliasAlreadyClaimedError(alias.name, holder_account_id=owner)
                credit.account_id = claim.account_id
                credit.account_source = "reviewed"
            claim.approved_credit_ids = sorted(map(str, ids))
        claim.status = status
        claim.resolved_at = Instant.now()
        claim.resolved_by_account_id = resolved_by_account_id
        await session.flush()
        # The claimant comes back with the claim so a resolution can name who was credited
        # instead of saying "the claimant"; one account, so `_load_accounts` costs two queries.
        claimant = await session.get(Account, claim.account_id)
        loaded = None if claimant is None else (await self._load_accounts(session, [claimant])).get(claim.account_id)
        return _hydrate_claim(claim, alias.name, loaded)

    # Verification-code reservations, attempt limits, and Java identity linking.

    def hash_verification_code(self, code: str) -> str:
        """Return a hex HMAC-SHA256 digest using the deployment pepper.

        Used for verification codes, merge codes, and reservation tokens. Human-entered
        codes need expiry and attempt limits in addition to hashing; see
        `docs/credential-hashing.md`.
        """
        # codeql[py/weak-sensitive-data-hashing]
        return hmac.digest(self._verification_code_pepper.encode(), code.encode(), hashlib.sha256).hex()

    def _reservation_holds(self, row: VerificationCodeModel, token: str) -> bool:
        """Whether *token* is the live hold on *row*."""
        if row.reserved_token is None or row.reserved_until is None:
            return False
        return hmac.compare_digest(row.reserved_token, self.hash_verification_code(token)) and (
            row.reserved_until > Instant.now()
        )

    async def reserve_verification_code(self, code: str, *, ttl_seconds: int) -> LinkReservation | None:
        """Hold a valid code and return its token, deadline, and current link preview.

        Return None for an unknown, expired, spent, or already reserved code. The hold
        stores no caller identity. Creator-credit ownership and the code's own expiry
        are rechecked at redemption; neither is frozen by the reservation.
        """
        timestamp = Instant.now()
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
        return LinkPreview(
            java_uuid=row.minecraft_uuid,
            username=row.username,
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
        timestamp = Instant.now()
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
            .returning(VerificationAttemptModel.locked_until, VerificationAttemptModel.consecutive_failures)
        )
        session = self._session()
        stored_lockout, failures = (await session.execute(statement)).one()
        # Only the attempt that reaches the cap resets the counter to zero.
        return stored_lockout if failures == 0 else None

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
        consent: PrivacyNoticeReceipt,
        reservation_token: str | None = None,
    ) -> VerificationLinkResult:
        """Consume a code and link its Java identity within the caller's transaction.

        The account must already exist. A supplied reservation_token must still match
        the live hold. Identity conflicts leave the code unconsumed. A successful link
        records consent and reconciles creator credit without taking a held alias from
        another account.

        Creator-credit ownership is checked again under locks because it may have
        changed since a reservation preview was shown.
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
        # Check both directions before consuming the code: the account's link and the UUID's owner.
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

        # Save the verified identity and consent receipt, then reconcile its creator credit.
        previous_name = None
        if existing_java is None:
            session.add(
                self._attach_identity(
                    account.id,
                    AccountIdentity.java(
                        verification_code.minecraft_uuid,
                        username=verification_code.username,
                        verified_at=Instant.now(),
                    ),
                )
            )
        else:
            previous_name = existing_java.display_name
            existing_java.display_name = verification_code.username
            existing_java.verified_at = Instant.now()
        account.revision += 1
        account.privacy_notice_version = consent.version
        account.privacy_notice_acknowledged_at = consent.granted_at
        verification_code.valid = False

        refresh = IdentityRefresh(
            account_id=account.id,
            java_uuid=verification_code.minecraft_uuid,
            current_name=verification_code.username,
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
        identity.verified_at = Instant.now()
        account.revision += 1
        return IdentityRefresh(
            account_id=account.id, java_uuid=java_uuid, current_name=username, previous_name=previous_name
        )

    async def replace_verification_code(
        self,
        *,
        minecraft_uuid: uuid.UUID,
        code: str,
        username: str,
    ) -> bool:
        """Replace the UUID's codes with a new one, keeping one row per UUID.

        Returns:
            False, with nothing inserted, when another UUID already holds a valid row with this code.
            Lookups match on the code alone, so a duplicate would be ambiguous.
        """
        session = self._session()
        await session.execute(
            delete(VerificationCodeModel).where(VerificationCodeModel.minecraft_uuid == minecraft_uuid)
        )
        inserted = await session.execute(
            insert(VerificationCodeModel)
            .values(minecraft_uuid=minecraft_uuid, code=self.hash_verification_code(code), username=username)
            .on_conflict_do_nothing(index_elements=["code"], index_where=text("valid"))
            .returning(VerificationCodeModel.id)
        )
        return inserted.scalar_one_or_none() is not None

    @staticmethod
    def _attach_identity(account_id: int, identity: AccountIdentity) -> AccountIdentity:
        identity.account_id = account_id
        identity.verified_at = identity.verified_at or Instant.now()
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
        return {account.id: _hydrate_account(account, grouped[account.id]) for account in accounts}
