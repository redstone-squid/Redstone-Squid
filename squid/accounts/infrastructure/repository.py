"""SQLAlchemy account repository, keyed by account rather than by identity provider."""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, column, delete, func, literal, or_, select, table, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.application.ports import VerificationLinkResult
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    CreatorProfileRecord,
    CreditedAlias,
    CreditPreview,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    MergeTicket,
    ProfileLink,
    ProfileUpdate,
    fold_creator_name,
)
from squid.accounts.errors import (
    AccountIdentityNotFoundError,
    AccountNotFoundError,
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    CreatorAliasNotFoundError,
    MinecraftAccountNotFoundError,
)
from squid.accounts.infrastructure.models import Account as AccountModel
from squid.accounts.infrastructure.models import AccountIdentity as AccountIdentityModel
from squid.accounts.infrastructure.models import AccountMergeTicket as AccountMergeTicketModel
from squid.accounts.infrastructure.models import AccountProfile as AccountProfileModel
from squid.accounts.infrastructure.models import CreatorAlias as CreatorAliasModel
from squid.accounts.infrastructure.models import CreatorAliasClaim as CreatorAliasClaimModel
from squid.accounts.infrastructure.models import PublicCreatorRedirect
from squid.accounts.infrastructure.models import VerificationAttempt as VerificationAttemptModel
from squid.accounts.infrastructure.models import VerificationCode as VerificationCodeModel
from squid.core.errors import DataIntegrityError
from squid.core.i18n import tr
from squid.persistence.types import InstantUTC, now
from squid.submissions.domain import FinalizationJobStatus
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.payload_integrity import submission_payload_digest

_BUILD_CREDITS = table("build_creators", column("alias_id"))
"""A lightweight handle on the one column of `build_creators` this module reads.

Deliberately not `squid.builds.infrastructure.models.BuildCreator`: importing that mapper pulls the
whole `Build` relationship graph into the accounts context, which both couples the two contexts and
makes importing this module depend on the builds taxonomy being imported first. All that is wanted
here is a count.
"""


_now = now
"""This module's long-standing local name for the shared storage-precision clock.

It was the first place the nanosecond/microsecond mismatch was found; the definition now lives
in `squid.persistence.types` so every persisted default gets the same treatment.
"""


@dataclass(frozen=True, slots=True)
class _AccountMergeContext:
    survivor: int
    absorbed: int

    @property
    def parameters(self) -> dict[str, int]:
        return {"survivor": self.survivor, "absorbed": self.absorbed}


@dataclass(frozen=True, slots=True)
class _AccountReference:
    table_name: str
    column_name: str


_IDENTITY_PROFILE_REFERENCES = (
    _AccountReference("minecraft_paper_installations", "owner_account_id"),
    _AccountReference("minecraft_player_challenges", "approved_by_account_id"),
    _AccountReference("minecraft_player_grants", "account_id"),
    _AccountReference("api_keys", "owner_account_id"),
    _AccountReference("api_keys", "created_by_account_id"),
    _AccountReference("web_sessions", "account_id"),
    _AccountReference("cli_device_enrollments", "approved_by_account_id"),
    _AccountReference("cli_devices", "account_id"),
    _AccountReference("account_identities", "account_id"),
)
_SUBMISSION_REFERENCES = (
    _AccountReference("builds", "submitter_account_id"),
    _AccountReference("submission_drafts", "owner_account_id"),
    _AccountReference("submission_draft_access", "account_id"),
    _AccountReference("submission_draft_changes", "actor_account_id"),
    _AccountReference("build_schematics", "rights_attested_by_account_id"),
)
_CREATOR_CREDIT_REFERENCES = (
    _AccountReference("creator_aliases", "account_id"),
    _AccountReference("creator_alias_claims", "resolved_by_account_id"),
)
_VOTING_REFERENCES = (_AccountReference("vote_sessions", "author_account_id"),)
_NOTIFICATION_REFERENCES = (
    _AccountReference("notification_subscriptions", "account_id"),
    _AccountReference("notifications", "account_id"),
    _AccountReference("notification_deliveries", "account_id"),
)
_PERMISSION_PROVENANCE_REFERENCES = (
    _AccountReference("permission_roles", "created_by_account_id"),
    _AccountReference("permission_role_patterns", "added_by_account_id"),
    _AccountReference("permission_role_includes", "added_by_account_id"),
    _AccountReference("permission_grants", "granted_by_account_id"),
    _AccountReference("permission_role_assignments", "granted_by_account_id"),
    _AccountReference("permission_audit_log", "actor_account_id"),
)

_COLLAPSE_DRAFT_ACCESS_SQL = """
DELETE FROM submission_draft_access AS access_row
WHERE access_row.id IN (
    SELECT CASE
        WHEN draft.owner_account_id = :absorbed THEN survivor_access.id
        ELSE absorbed_access.id
    END
    FROM submission_draft_access AS survivor_access
    JOIN submission_draft_access AS absorbed_access
      ON absorbed_access.draft_id = survivor_access.draft_id
    JOIN submission_drafts AS draft ON draft.id = survivor_access.draft_id
    WHERE survivor_access.account_id = :survivor
      AND absorbed_access.account_id = :absorbed
)
"""
_COLLAPSE_ALIAS_CLAIMS_SQL = """
DELETE FROM creator_alias_claims AS absorbed_claim
USING creator_alias_claims AS survivor_claim
WHERE absorbed_claim.account_id = :absorbed
  AND survivor_claim.account_id = :survivor
  AND absorbed_claim.alias_id = survivor_claim.alias_id
  AND absorbed_claim.status = 'pending'
  AND survivor_claim.status = 'pending'
"""
_COLLAPSE_VOTES_SQL = """
DELETE FROM votes AS absorbed_vote
USING votes AS survivor_vote
WHERE absorbed_vote.account_id = :absorbed
  AND survivor_vote.account_id = :survivor
  AND absorbed_vote.vote_session_id = survivor_vote.vote_session_id
"""
_MERGE_NOTIFICATION_PROFILE_SQL = """
INSERT INTO notification_profiles (
    account_id,
    web_enabled,
    dm_enabled,
    dm_suspended_at,
    created_at,
    updated_at
)
SELECT
    :survivor,
    web_enabled,
    dm_enabled,
    dm_suspended_at,
    created_at,
    updated_at
FROM notification_profiles
WHERE account_id = :absorbed
ON CONFLICT (account_id) DO UPDATE SET
    web_enabled = notification_profiles.web_enabled OR EXCLUDED.web_enabled,
    dm_enabled = notification_profiles.dm_enabled OR EXCLUDED.dm_enabled,
    dm_suspended_at = COALESCE(notification_profiles.dm_suspended_at, EXCLUDED.dm_suspended_at)
"""
_COLLAPSE_NOTIFICATION_SUBSCRIPTIONS_SQL = """
DELETE FROM notification_subscriptions AS absorbed_subscription
USING notification_subscriptions AS survivor_subscription
WHERE absorbed_subscription.account_id = :absorbed
  AND survivor_subscription.account_id = :survivor
  AND absorbed_subscription.kind = survivor_subscription.kind
  AND absorbed_subscription.subject_id IS NOT DISTINCT FROM survivor_subscription.subject_id
  AND absorbed_subscription.filter IS NOT DISTINCT FROM survivor_subscription.filter
  AND absorbed_subscription.enabled = survivor_subscription.enabled
"""
_MERGE_PERMISSION_EFFECTS_SQL = """
UPDATE permission_grants AS survivor_grant
SET effect = LEAST(survivor_grant.effect, absorbed_grant.effect)
FROM permission_grants AS absorbed_grant
WHERE survivor_grant.subject_account_id = :survivor
  AND absorbed_grant.subject_account_id = :absorbed
  AND absorbed_grant.pattern = survivor_grant.pattern
  AND absorbed_grant.scope_guild_id IS NOT DISTINCT FROM survivor_grant.scope_guild_id
"""
_COLLAPSE_PERMISSION_GRANTS_SQL = """
DELETE FROM permission_grants AS absorbed_grant
USING permission_grants AS survivor_grant
WHERE absorbed_grant.subject_account_id = :absorbed
  AND survivor_grant.subject_account_id = :survivor
  AND absorbed_grant.pattern = survivor_grant.pattern
  AND absorbed_grant.scope_guild_id IS NOT DISTINCT FROM survivor_grant.scope_guild_id
"""
_COLLAPSE_PERMISSION_ROLE_ASSIGNMENTS_SQL = """
DELETE FROM permission_role_assignments AS absorbed_assignment
USING permission_role_assignments AS survivor_assignment
WHERE absorbed_assignment.subject_account_id = :absorbed
  AND survivor_assignment.subject_account_id = :survivor
  AND absorbed_assignment.role_id = survivor_assignment.role_id
  AND absorbed_assignment.scope_guild_id IS NOT DISTINCT FROM survivor_assignment.scope_guild_id
"""


def _to_identity(model: AccountIdentityModel) -> AccountIdentity:
    return AccountIdentity(
        id=model.id,
        provider=IdentityProvider(model.provider),
        subject=model.subject,
        display_name=model.display_name,
        verified_at=model.verified_at,
        is_public=model.is_public,
        avatar_key=model.avatar_key,
    )


def _to_profile(model: AccountProfileModel) -> AccountProfile:
    return AccountProfile(
        account_id=model.account_id,
        display_name=model.display_name,
        bio=model.bio,
        pronouns=model.pronouns,
        links=tuple(ProfileLink(label=link["label"], url=link["url"]) for link in model.links),
        hidden=model.hidden,
        avatar_identity_id=model.avatar_identity_id,
        updated_at=model.updated_at,
    )


def _to_merge_ticket(model: AccountMergeTicketModel) -> MergeTicket:
    return MergeTicket(
        account_id=model.account_id,
        created_at=model.created_at,
        expires_at=model.expires_at,
    )


def _to_account(model: AccountModel, identities: Sequence[AccountIdentityModel]) -> Account:
    consent = None
    if model.consent_version is not None and model.consented_at is not None:
        consent = AccountConsent(version=model.consent_version, granted_at=model.consented_at)
    return Account(
        identities=tuple(_to_identity(identity) for identity in identities),
        consent=consent,
        id=model.id,
        created_at=model.created_at,
        public_creator_id=model.public_creator_id,
    )


def _to_alias(alias: CreatorAliasModel, public_creator_id: uuid.UUID | None = None) -> CreatorAlias:
    return CreatorAlias(
        id=alias.id,
        name=alias.name,
        account_id=alias.account_id,
        claimed_at=alias.claimed_at,
        claim_method=None if alias.claim_method is None else ClaimMethod(alias.claim_method),
        public_creator_id=public_creator_id,
    )


def _to_claim(claim: CreatorAliasClaimModel, alias_name: str, claimant: Account | None = None) -> AliasClaim:
    return AliasClaim(
        id=claim.id,
        alias_id=claim.alias_id,
        alias_name=alias_name,
        account_id=claim.account_id,
        status=ClaimStatus(claim.status),
        created_at=claim.created_at,
        resolved_at=claim.resolved_at,
        resolved_by_account_id=claim.resolved_by_account_id,
        claimant=claimant,
    )


class AccountRepository:
    """Persist accounts, external identities, creator aliases, and verification codes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], verification_code_pepper: str):
        self._session_factory = session_factory
        self._verification_code_pepper = verification_code_pepper

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        """Insert one account and its verified identities atomically."""
        async with self._session_factory.begin() as session:
            account = AccountModel(
                consent_version=None if consent is None else consent.version,
                consented_at=None if consent is None else consent.granted_at,
            )
            session.add(account)
            await session.flush()
            session.add(AccountProfileModel(account_id=account.id))
            rows = [self._identity_model(account.id, identity) for identity in identities]
            session.add_all(rows)
            # The flush populates `id` from RETURNING, and every other column `_to_identity`
            # reads was set here, so the per-row refresh this used to do bought one round trip
            # each for values already in hand.
            await session.flush()
            return _to_account(account, sorted(rows, key=lambda row: (row.provider, row.id)))

    async def get_by_id(self, account_id: int) -> Account | None:
        """Return an account by internal identifier."""
        async with self._session_factory() as session:
            model = await session.get(AccountModel, account_id)
            return None if model is None else await self._load_account(session, model)

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return several accounts, with their identities, in two queries regardless of count.

        Anything presenting a list of accounts — the staff claim queue, for one — needs this
        rather than a `get_by_id` per row.
        """
        if not account_ids:
            return {}
        async with self._session_factory() as session:
            models = (
                await session.scalars(
                    select(AccountModel).where(AccountModel.id.in_(set(account_ids))).order_by(AccountModel.id)
                )
            ).all()
            return await self._load_accounts(session, models)

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        """Return the account holding one canonical provider subject."""
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AccountModel)
                .join(AccountIdentityModel, AccountIdentityModel.account_id == AccountModel.id)
                .where(AccountIdentityModel.provider == provider, AccountIdentityModel.subject == subject)
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
        async with self._session_factory.begin() as session:
            existing = await self._find_account(session, identity.provider, identity.subject)
            if existing is not None:
                return await self._load_account(session, existing)

            candidate = AccountModel(
                consent_version=None if consent is None else consent.version,
                consented_at=None if consent is None else consent.granted_at,
            )
            session.add(candidate)
            await session.flush()
            # Rides the same transaction as the candidate account: if the identity insert loses
            # the race below, deleting the candidate takes this row with it via CASCADE.
            session.add(AccountProfileModel(account_id=candidate.id))
            inserted_id = await session.scalar(
                insert(AccountIdentityModel)
                .values(
                    account_id=candidate.id,
                    provider=identity.provider,
                    subject=identity.subject,
                    verified_at=_now(),
                )
                .on_conflict_do_nothing(index_elements=[AccountIdentityModel.provider, AccountIdentityModel.subject])
                .returning(AccountIdentityModel.id)
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
        async with self._session_factory.begin() as session:
            model = await session.get(AccountModel, account_id, with_for_update=True)
            if model is None:
                raise AccountNotFoundError(account_id)
            model.consent_version = consent.version
            model.consented_at = consent.granted_at
            await session.flush()
            return await self._load_account(session, model)

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None:
        """Remove one identity by internal id, returning it, or `None` when it is not this account's.

        Addressed by id rather than by provider because an account can hold two identities from the
        same provider: a merge moves every row across, and picking "the Discord one" would then be
        a coin flip.
        """
        async with self._session_factory.begin() as session:
            removed = await session.scalar(
                delete(AccountIdentityModel)
                .where(
                    AccountIdentityModel.id == identity_id,
                    AccountIdentityModel.account_id == account_id,
                )
                .returning(AccountIdentityModel)
            )
            # An avatar sourced from this identity clears itself: the foreign key is ON DELETE SET
            # NULL precisely so nothing has to remember to do it here.
            return None if removed is None else _to_identity(removed)

    async def count_identities(self, account_id: int) -> int:
        """Return how many identities an account currently holds."""
        async with self._session_factory() as session:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(AccountIdentityModel)
                    .where(AccountIdentityModel.account_id == account_id)
                )
                or 0
            )

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one identity on the account's public profile."""
        async with self._session_factory.begin() as session:
            updated = await session.scalar(
                update(AccountIdentityModel)
                .where(
                    AccountIdentityModel.id == identity_id,
                    AccountIdentityModel.account_id == account_id,
                )
                .values(is_public=is_public)
                .returning(AccountIdentityModel)
            )
            if updated is None:
                raise AccountIdentityNotFoundError(identity_id, account_id=account_id)
            return _to_identity(updated)

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Record the provider rendering key an avatar URL needs.

        Separate from the profile write because it is not a user edit: the bot refreshes it
        opportunistically whenever it happens to hold a fresh `discord.User`.
        """
        async with self._session_factory.begin() as session:
            await session.execute(
                update(AccountIdentityModel)
                .where(
                    AccountIdentityModel.id == identity_id,
                    AccountIdentityModel.account_id == account_id,
                )
                .values(avatar_key=avatar_key)
            )

    async def get_profile(self, account_id: int) -> AccountProfile | None:
        """Return an account's own profile, visibility flags and all."""
        async with self._session_factory() as session:
            model = await session.get(AccountProfileModel, account_id)
            # Every account is given a profile row, but a read that predates its creation must
            # render as an empty profile rather than a 500.
            if model is None:
                exists = await session.scalar(select(AccountModel.id).where(AccountModel.id == account_id))
                return None if exists is None else AccountProfile.empty(account_id)
            return _to_profile(model)

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile:
        """Apply a validated partial edit, creating the profile row if it is somehow missing."""
        async with self._session_factory.begin() as session:
            account = await session.get(AccountModel, account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            model = await session.get(AccountProfileModel, account_id, with_for_update=True)
            if model is None:
                model = AccountProfileModel(account_id=account_id)
                session.add(model)
                await session.flush()
            avatar_identity_id = update_request.avatar_identity_id
            # An `int` is the only case that needs checking: UNSET leaves the avatar alone and
            # `None` clears it, and neither can point at somebody else's identity.
            if isinstance(avatar_identity_id, int):
                # The foreign key cannot express "and it must be yours" without a composite key
                # that would break ON DELETE SET NULL, so ownership is checked here.
                owner = await session.scalar(
                    select(AccountIdentityModel.account_id).where(AccountIdentityModel.id == avatar_identity_id)
                )
                if owner != account_id:
                    raise AccountIdentityNotFoundError(avatar_identity_id, account_id=account_id)
            updated = update_request.apply(_to_profile(model))
            model.display_name = updated.display_name
            model.bio = updated.bio
            model.pronouns = updated.pronouns
            model.links = [{"label": link.label, "url": link.url} for link in updated.links]
            model.hidden = updated.hidden
            model.avatar_identity_id = updated.avatar_identity_id
            model.updated_at = _now()
            await session.flush()
            return _to_profile(model)

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Reset a profile to the state a new account starts with.

        Staff moderation. Clearing leaves the profile *visible*: `hidden` is the owner's control,
        and a takedown that also hid the page would silently take that decision away from them.
        """
        async with self._session_factory.begin() as session:
            model = await session.get(AccountProfileModel, account_id, with_for_update=True)
            if model is None:
                account = await session.get(AccountModel, account_id)
                if account is None:
                    raise AccountNotFoundError(account_id)
                model = AccountProfileModel(account_id=account_id)
                session.add(model)
            model.display_name = None
            model.bio = None
            model.pronouns = None
            model.links = []
            model.hidden = False
            model.avatar_identity_id = None
            model.updated_at = _now()
            await session.flush()
            return _to_profile(model)

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        """Mint the account's merge ticket, replacing any live one.

        Replace rather than insert: one live ticket per account is what keeps a short code safe,
        and minting a second would double a guesser's target surface for no user-visible gain.
        """
        now = _now()
        expires_at = now.add(seconds=ttl_seconds)
        digest = self.hash_verification_code(code)
        async with self._session_factory.begin() as session:
            account = await session.get(AccountModel, account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            await session.execute(
                insert(AccountMergeTicketModel)
                .values(account_id=account_id, code_digest=digest, created_at=now, expires_at=expires_at)
                .on_conflict_do_update(
                    index_elements=[AccountMergeTicketModel.account_id],
                    set_={"code_digest": digest, "created_at": now, "expires_at": expires_at},
                )
            )
            return MergeTicket(account_id=account_id, created_at=now, expires_at=expires_at)

    async def peek_merge_ticket(self, code: str) -> MergeTicket | None:
        """Return a live ticket without spending it, for a confirmation screen."""
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AccountMergeTicketModel).where(
                    AccountMergeTicketModel.code_digest == self.hash_verification_code(code),
                    AccountMergeTicketModel.expires_at > _now(),
                )
            )
            return None if row is None else _to_merge_ticket(row)

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None:
        """Spend a live ticket, returning it, or `None` when there is nothing to spend.

        The delete is the redemption: a code that reaches here twice fails the second time, so a
        replayed request cannot merge a second account into the survivor.
        """
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                delete(AccountMergeTicketModel)
                .where(
                    AccountMergeTicketModel.code_digest == self.hash_verification_code(code),
                    AccountMergeTicketModel.expires_at > _now(),
                )
                .returning(AccountMergeTicketModel)
            )
            return None if row is None else _to_merge_ticket(row)

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge:
        """Move resources to one account and preserve the absorbed public ID as a redirect."""
        first, second = sorted((surviving_account_id, absorbed_account_id))
        async with self._session_factory.begin() as session:
            locked = (
                await session.scalars(
                    select(AccountModel)
                    .where(AccountModel.id.in_((first, second)))
                    .order_by(AccountModel.id)
                    .with_for_update()
                )
            ).all()
            by_id = {account.id: account for account in locked}
            survivor = by_id.get(surviving_account_id)
            absorbed = by_id.get(absorbed_account_id)
            if survivor is None:
                raise AccountNotFoundError(surviving_account_id)
            if absorbed is None:
                raise AccountNotFoundError(absorbed_account_id)

            await self._merge_references(session, surviving_account_id, absorbed_account_id)
            result = AccountMerge(
                surviving_account_id=surviving_account_id,
                absorbed_account_id=absorbed_account_id,
                surviving_public_creator_id=survivor.public_creator_id,
                redirected_public_creator_id=absorbed.public_creator_id,
            )
            await _retire_absorbed_account(session, survivor, absorbed)
            return result

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        """Return a case-insensitive creator alias."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CreatorAliasModel, AccountModel.public_creator_id)
                    .outerjoin(AccountModel, AccountModel.id == CreatorAliasModel.account_id)
                    .where(CreatorAliasModel.normalized_name == fold_creator_name(name))
                )
            ).one_or_none()
            return None if row is None else _to_alias(row[0], row[1])

    async def get_creator_profile(self, public_id: uuid.UUID) -> CreatorProfile | None:
        """Return aliases for a public ID, including a permanent merge redirect."""
        async with self._session_factory() as session:
            account = await session.scalar(select(AccountModel).where(AccountModel.public_creator_id == public_id))
            canonical_id = public_id
            if account is None:
                account = await session.scalar(
                    select(AccountModel)
                    .join(PublicCreatorRedirect, PublicCreatorRedirect.target_account_id == AccountModel.id)
                    .where(PublicCreatorRedirect.retired_public_creator_id == public_id)
                )
                if account is None:
                    return None
                canonical_id = account.public_creator_id
            aliases = (
                await session.scalars(
                    select(CreatorAliasModel.name)
                    .where(CreatorAliasModel.account_id == account.id)
                    .order_by(CreatorAliasModel.normalized_name)
                )
            ).all()
            return CreatorProfile(public_id=public_id, aliases=tuple(aliases), canonical_public_id=canonical_id)

    async def get_creator_profile_record(self, public_id: uuid.UUID) -> CreatorProfileRecord | None:
        """Return everything known about one creator, before visibility is applied.

        Deliberately unfiltered: `present_public_profile` decides what a stranger sees, and it can
        only be the one authority on that if persistence hands it the whole truth.
        """
        async with self._session_factory() as session:
            account = await session.scalar(select(AccountModel).where(AccountModel.public_creator_id == public_id))
            canonical_id = public_id
            if account is None:
                account = await session.scalar(
                    select(AccountModel)
                    .join(PublicCreatorRedirect, PublicCreatorRedirect.target_account_id == AccountModel.id)
                    .where(PublicCreatorRedirect.retired_public_creator_id == public_id)
                )
                if account is None:
                    return None
                canonical_id = account.public_creator_id

            profile_model = await session.get(AccountProfileModel, account.id)
            profile = AccountProfile.empty(account.id) if profile_model is None else _to_profile(profile_model)
            identities = (
                await session.scalars(
                    select(AccountIdentityModel)
                    .where(AccountIdentityModel.account_id == account.id)
                    .order_by(AccountIdentityModel.provider, AccountIdentityModel.id)
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
                    select(CreatorAliasModel.name, func.coalesce(credit_counts.c.build_count, 0))
                    .outerjoin(credit_counts, credit_counts.c.alias_id == CreatorAliasModel.id)
                    .where(CreatorAliasModel.account_id == account.id)
                    .order_by(CreatorAliasModel.normalized_name)
                )
            ).all()
            return CreatorProfileRecord(
                public_id=public_id,
                account_id=account.id,
                profile=profile,
                identities=tuple(_to_identity(identity) for identity in identities),
                aliases=tuple(CreditedAlias(name=name, build_count=count) for name, count in alias_rows),
                joined_at=account.created_at,
                canonical_public_id=canonical_id,
            )

    async def claim_unclaimed_alias(self, *, account_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Claim an alias only if it remains unclaimed at update time."""
        async with self._session_factory.begin() as session:
            alias = await session.scalar(
                update(CreatorAliasModel)
                .where(
                    CreatorAliasModel.normalized_name == fold_creator_name(name),
                    CreatorAliasModel.account_id.is_(None),
                )
                .values(account_id=account_id, claimed_at=_now(), claim_method=method)
                .returning(CreatorAliasModel)
            )
            public_id = await session.scalar(
                select(AccountModel.public_creator_id).where(AccountModel.id == account_id)
            )
            return None if alias is None else _to_alias(alias, public_id)

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim:
        """Open a pending alias claim, reusing an existing matching request."""
        async with self._session_factory.begin() as session:
            alias = await session.scalar(
                select(CreatorAliasModel).where(CreatorAliasModel.normalized_name == fold_creator_name(name))
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
                select(CreatorAliasClaimModel).where(
                    CreatorAliasClaimModel.alias_id == alias.id,
                    CreatorAliasClaimModel.account_id == account_id,
                    CreatorAliasClaimModel.status == ClaimStatus.PENDING,
                )
            )
            if existing is not None:
                return _to_claim(existing, alias.name)
            claim = CreatorAliasClaimModel(alias_id=alias.id, account_id=account_id)
            session.add(claim)
            await session.flush()
            return _to_claim(claim, alias.name)

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        """Return one claim by identifier."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CreatorAliasClaimModel, CreatorAliasModel.name)
                    .join(CreatorAliasModel, CreatorAliasModel.id == CreatorAliasClaimModel.alias_id)
                    .where(CreatorAliasClaimModel.id == claim_id)
                )
            ).one_or_none()
            return None if row is None else _to_claim(row[0], row[1])

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List claims awaiting staff review, oldest first.

        *with_claimants* loads every claimant's account in the same two extra queries no
        matter how long the queue is, which is what presenting a claimant as anything better
        than an internal account ID needs.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(CreatorAliasClaimModel, CreatorAliasModel.name)
                    .join(CreatorAliasModel, CreatorAliasModel.id == CreatorAliasClaimModel.alias_id)
                    .where(CreatorAliasClaimModel.status == ClaimStatus.PENDING)
                    .order_by(CreatorAliasClaimModel.created_at)
                )
            ).all()
            claimants: dict[int, Account] = {}
            if with_claimants and rows:
                models = (
                    await session.scalars(
                        select(AccountModel)
                        .where(AccountModel.id.in_({claim.account_id for claim, _ in rows}))
                        .order_by(AccountModel.id)
                    )
                ).all()
                claimants = await self._load_accounts(session, models)
            return tuple(_to_claim(claim, name, claimants.get(claim.account_id)) for claim, name in rows)

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
        async with self._session_factory.begin() as session:
            claim = await session.get(CreatorAliasClaimModel, claim_id, with_for_update=True)
            if claim is None or claim.status != ClaimStatus.PENDING:
                raise ClaimNotFoundError(claim_id)
            alias = await session.get(CreatorAliasModel, claim.alias_id, with_for_update=True)
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
                alias.claimed_at = _now()
                alias.claim_method = ClaimMethod.STAFF_APPROVED
            claim.status = status
            claim.resolved_at = _now()
            claim.resolved_by_account_id = resolved_by_account_id
            await session.flush()
            # The claimant comes back with the claim so a resolution can name who was credited
            # instead of saying "the claimant"; one account, so `_load_accounts` costs two queries.
            claimant = await session.get(AccountModel, claim.account_id)
            loaded = (
                None if claimant is None else (await self._load_accounts(session, [claimant])).get(claim.account_id)
            )
            return _to_claim(claim, alias.name, loaded)

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
        return await session.scalar(select(AccountModel.public_creator_id).where(AccountModel.id == account_id))

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
        now = _now()
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(VerificationCodeModel)
                .where(
                    VerificationCodeModel.expires > now,
                    VerificationCodeModel.code == self.hash_verification_code(code),
                    VerificationCodeModel.valid.is_(True),
                    or_(
                        VerificationCodeModel.reserved_until.is_(None),
                        VerificationCodeModel.reserved_until <= now,
                    ),
                )
                .with_for_update()
            )
            if row is None:
                return None

            token = secrets.token_urlsafe(32)
            expires_at = now.add(seconds=ttl_seconds)
            row.reserved_token = self.hash_verification_code(token)
            row.reserved_until = expires_at
            preview = await self._preview_link(session, row)
            return LinkReservation(token=token, expires_at=expires_at, preview=preview)

    async def _preview_link(self, session: AsyncSession, row: VerificationCodeModel) -> LinkPreview:
        """Describe the effects of redeeming *row* without redeeming it."""
        java_subject = str(row.minecraft_uuid)
        held_elsewhere = await session.scalar(
            select(AccountIdentityModel.id).where(
                AccountIdentityModel.provider == IdentityProvider.JAVA,
                AccountIdentityModel.subject == java_subject,
            )
        )
        alias_row = (
            await session.execute(
                select(CreatorAliasModel, AccountModel.public_creator_id)
                .outerjoin(AccountModel, CreatorAliasModel.account_id == AccountModel.id)
                .where(CreatorAliasModel.normalized_name == fold_creator_name(row.username))
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
        async with self._session_factory.begin() as session:
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
        async with self._session_factory() as session:
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
        now = _now()
        locked_until = now.add(seconds=lockout_seconds)
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
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[VerificationAttemptModel.provider, VerificationAttemptModel.subject],
                set_={
                    "consecutive_failures": case(
                        (reached_cap, 0), else_=VerificationAttemptModel.consecutive_failures + 1
                    ),
                    "locked_until": case((reached_cap, lockout_bind), else_=VerificationAttemptModel.locked_until),
                    "updated_at": now,
                },
            )
            .returning(VerificationAttemptModel.locked_until)
        )
        async with self._session_factory.begin() as session:
            stored_lockout = await session.scalar(statement)
        # Only report a lockout *this* call started. Comparing for equality rather than for being in
        # the future distinguishes "I caused this" from "one was already running", which matters both
        # for the caller's log line and for a race: two attempts crossing the cap together compute
        # instants a microsecond apart, so exactly one sees its own value survive. Exact comparison is
        # sound because `_now()` floors to the precision `timestamptz` keeps.
        return locked_until if stored_lockout == locked_until else None

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None:
        """Forget an identity's failures after a successful redemption."""
        async with self._session_factory.begin() as session:
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
        async with self._session_factory.begin() as session:
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

            account = await session.get(AccountModel, account_id, with_for_update=True)
            if account is None:
                raise AccountNotFoundError(account_id)
            java_subject = str(verification_code.minecraft_uuid)
            java_holder = await self._find_account(session, IdentityProvider.JAVA, java_subject, for_update=True)
            existing_java = await session.scalar(
                select(AccountIdentityModel).where(
                    AccountIdentityModel.account_id == account.id,
                    AccountIdentityModel.provider == IdentityProvider.JAVA,
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
                    self._identity_model(
                        account.id,
                        AccountIdentity.java(
                            verification_code.minecraft_uuid,
                            username=verification_code.username,
                            verified_at=_now(),
                        ),
                    )
                )
            else:
                previous_name = existing_java.display_name
                existing_java.display_name = verification_code.username
                existing_java.verified_at = _now()
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
        async with self._session_factory.begin() as session:
            account = await session.get(AccountModel, account_id, with_for_update=True)
            if account is None:
                raise AccountNotFoundError(account_id)
            identity = await session.scalar(
                select(AccountIdentityModel)
                .where(
                    AccountIdentityModel.account_id == account_id,
                    AccountIdentityModel.provider == IdentityProvider.JAVA,
                    AccountIdentityModel.subject == str(java_uuid),
                )
                .with_for_update()
            )
            if identity is None:
                raise MinecraftAccountNotFoundError(java_uuid)
            previous_name = identity.display_name
            identity.display_name = username
            identity.verified_at = _now()
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
        account: AccountModel,
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
            select(CreatorAliasModel).where(CreatorAliasModel.normalized_name == folded).with_for_update()
        )
        if alias is None:
            # No build has credited this name yet. Create it so the public profile shows the
            # current name rather than lagging until someone happens to credit it.
            inserted = await session.scalar(
                insert(CreatorAliasModel)
                .values(
                    name=username,
                    account_id=account.id,
                    claimed_at=_now(),
                    claim_method=ClaimMethod.VERIFIED_IGN,
                )
                .on_conflict_do_nothing(index_elements=[CreatorAliasModel.normalized_name])
                .returning(CreatorAliasModel)
            )
            alias = inserted or await session.scalar(
                select(CreatorAliasModel).where(CreatorAliasModel.normalized_name == folded).with_for_update()
            )
            assert alias is not None
        if alias.account_id is None:
            alias.account_id = account.id
            alias.claimed_at = _now()
            alias.claim_method = ClaimMethod.VERIFIED_IGN
            claimed = _to_alias(alias, account.public_creator_id)
        elif alias.account_id == account.id:
            claimed = _to_alias(alias, account.public_creator_id)
        else:
            # Someone else is credited under this name. Never transfer on a rename; open a
            # claim so it lands in the staff queue instead of vanishing.
            contested = _to_alias(alias)
            claim = await session.scalar(
                insert(CreatorAliasClaimModel)
                .values(alias_id=alias.id, account_id=account.id, status=ClaimStatus.PENDING)
                # `creator_alias_claims_one_pending_per_account` is partial, so the predicate has
                # to be restated here or Postgres cannot match the conflict to it.
                .on_conflict_do_nothing(
                    index_elements=[CreatorAliasClaimModel.alias_id, CreatorAliasClaimModel.account_id],
                    index_where=text("status = 'pending'"),
                )
                .returning(CreatorAliasClaimModel)
            )
            claim = claim or await session.scalar(
                select(CreatorAliasClaimModel).where(
                    CreatorAliasClaimModel.alias_id == alias.id,
                    CreatorAliasClaimModel.account_id == account.id,
                    CreatorAliasClaimModel.status == ClaimStatus.PENDING,
                )
            )
            opened_claim = None if claim is None else _to_claim(claim, alias.name)

        await session.flush()
        retained = (
            await session.scalars(
                select(CreatorAliasModel.name)
                .where(CreatorAliasModel.account_id == account.id, CreatorAliasModel.normalized_name != folded)
                .order_by(CreatorAliasModel.normalized_name)
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
        async with self._session_factory.begin() as session:
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
    def _identity_model(account_id: int, identity: AccountIdentity) -> AccountIdentityModel:
        return AccountIdentityModel(
            account_id=account_id,
            provider=identity.provider,
            subject=identity.subject,
            display_name=identity.display_name,
            verified_at=identity.verified_at or _now(),
        )

    @staticmethod
    async def _find_account(
        session: AsyncSession,
        provider: IdentityProvider,
        subject: str,
        *,
        for_update: bool = False,
    ) -> AccountModel | None:
        statement = (
            select(AccountModel)
            .join(AccountIdentityModel, AccountIdentityModel.account_id == AccountModel.id)
            .where(AccountIdentityModel.provider == provider, AccountIdentityModel.subject == subject)
        )
        if for_update:
            statement = statement.with_for_update(of=AccountModel)
        return await session.scalar(statement)

    @classmethod
    async def _load_account(cls, session: AsyncSession, account: AccountModel) -> Account:
        loaded = await cls._load_accounts(session, [account])
        return loaded[account.id]

    @staticmethod
    async def _load_accounts(session: AsyncSession, accounts: Sequence[AccountModel]) -> dict[int, Account]:
        """Load identities for several accounts in one query.

        The mapping stays explicit — the domain objects are frozen dataclasses and must not
        learn about SQLAlchemy — but the *fetch* does not have to be per row, which is what
        made any multi-account read an N+1.
        """
        if not accounts:
            return {}
        account_ids = [account.id for account in accounts]
        identities = (
            await session.scalars(
                select(AccountIdentityModel)
                .where(AccountIdentityModel.account_id.in_(account_ids))
                .order_by(AccountIdentityModel.provider, AccountIdentityModel.id)
            )
        ).all()
        grouped: dict[int, list[AccountIdentityModel]] = {account_id: [] for account_id in account_ids}
        for identity in identities:
            grouped[identity.account_id].append(identity)
        return {account.id: _to_account(account, grouped[account.id]) for account in accounts}

    @staticmethod
    async def _merge_references(session: AsyncSession, survivor: int, absorbed: int) -> None:
        """Move every account-keyed resource through named phases in this transaction."""
        context = _AccountMergeContext(survivor, absorbed)
        await _merge_submissions(session, context)
        await _merge_identities_and_profiles(session, context)
        await _merge_creator_credit(session, context)
        await _merge_voting(session, context)
        await _merge_notifications(session, context)
        await _merge_permissions(session, context)


async def _merge_submissions(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Move builds, drafts, artifact attestations, and retained finalization owners."""
    draft_ids = tuple(
        (
            await session.scalars(
                select(SubmissionDraft.id)
                .where(SubmissionDraft.owner_account_id == context.absorbed)
                .order_by(SubmissionDraft.id)
                .with_for_update()
            )
        ).all()
    )
    await _canonicalize_finalization_job_owners(session, draft_ids, context.survivor, context.absorbed)
    await _execute_merge_sql(session, _COLLAPSE_DRAFT_ACCESS_SQL, context)
    await _move_account_references(session, _SUBMISSION_REFERENCES, context)


async def _merge_identities_and_profiles(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Move external identities and credential ownership while retaining the survivor profile."""
    await _move_account_references(session, _IDENTITY_PROFILE_REFERENCES, context)


async def _merge_creator_credit(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Move creator aliases and collapse duplicate pending claims."""
    await _move_account_references(session, _CREATOR_CREDIT_REFERENCES, context)
    await _execute_merge_sql(session, _COLLAPSE_ALIAS_CLAIMS_SQL, context)
    await _move_account_reference(session, _AccountReference("creator_alias_claims", "account_id"), context)


async def _merge_voting(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Move authored sessions and retain one ballot per merged voter."""
    await _move_account_references(session, _VOTING_REFERENCES, context)
    await _execute_merge_sql(session, _COLLAPSE_VOTES_SQL, context)
    await _move_account_reference(session, _AccountReference("votes", "account_id"), context)


async def _merge_notifications(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Coalesce delivery preferences and duplicate subscriptions before moving inbox facts."""
    await _execute_merge_sql(session, _MERGE_NOTIFICATION_PROFILE_SQL, context)
    profiles = table("notification_profiles", column("account_id"))
    await session.execute(delete(profiles).where(profiles.c.account_id == context.absorbed))
    await _execute_merge_sql(session, _COLLAPSE_NOTIFICATION_SUBSCRIPTIONS_SQL, context)
    await _move_account_references(session, _NOTIFICATION_REFERENCES, context)


async def _merge_permissions(session: AsyncSession, context: _AccountMergeContext) -> None:
    """Move permission provenance and collapse subject collisions to the restrictive effect."""
    await _move_account_references(session, _PERMISSION_PROVENANCE_REFERENCES, context)
    await _execute_merge_sql(session, _MERGE_PERMISSION_EFFECTS_SQL, context)
    await _execute_merge_sql(session, _COLLAPSE_PERMISSION_GRANTS_SQL, context)
    await _move_account_reference(session, _AccountReference("permission_grants", "subject_account_id"), context)
    await _execute_merge_sql(session, _COLLAPSE_PERMISSION_ROLE_ASSIGNMENTS_SQL, context)
    await _move_account_reference(
        session,
        _AccountReference("permission_role_assignments", "subject_account_id"),
        context,
    )
    audit = table("permission_audit_log", column("subject_kind"), column("subject_id"))
    await session.execute(
        update(audit)
        .where(audit.c.subject_kind == "account", audit.c.subject_id == context.absorbed)
        .values(subject_id=context.survivor)
    )


async def _move_account_references(
    session: AsyncSession,
    references: Sequence[_AccountReference],
    context: _AccountMergeContext,
) -> None:
    for reference in references:
        await _move_account_reference(session, reference, context)


async def _move_account_reference(
    session: AsyncSession,
    reference: _AccountReference,
    context: _AccountMergeContext,
) -> None:
    target = table(reference.table_name, column(reference.column_name))
    account_column = target.c[reference.column_name]
    await session.execute(
        update(target).where(account_column == context.absorbed).values({reference.column_name: context.survivor})
    )


async def _execute_merge_sql(
    session: AsyncSession,
    statement: str,
    context: _AccountMergeContext,
) -> None:
    await session.execute(text(statement), context.parameters)


async def _retire_absorbed_account(
    session: AsyncSession,
    survivor: AccountModel,
    absorbed: AccountModel,
) -> None:
    """Finish the merge by preserving public redirects and deleting the source row."""
    await session.execute(
        update(PublicCreatorRedirect)
        .where(PublicCreatorRedirect.target_account_id == absorbed.id)
        .values(target_account_id=survivor.id)
    )
    session.add(
        PublicCreatorRedirect(
            retired_public_creator_id=absorbed.public_creator_id,
            target_account_id=survivor.id,
        )
    )
    await session.delete(absorbed)


async def _canonicalize_finalization_job_owners(
    session: AsyncSession,
    draft_ids: Sequence[uuid.UUID],
    survivor: int,
    absorbed: int,
) -> None:
    """Rewrite retained payload owners and fence claims that escaped before the merge lock."""
    if not draft_ids:
        return
    jobs = tuple(
        (
            await session.scalars(
                select(SubmissionFinalizationJob)
                .where(
                    SubmissionFinalizationJob.draft_id.in_(draft_ids),
                    SubmissionFinalizationJob.payload.is_not(None),
                )
                .order_by(SubmissionFinalizationJob.draft_id, SubmissionFinalizationJob.id)
                .with_for_update()
            )
        ).all()
    )
    rewritten_at = _now()
    for job in jobs:
        payload = job.payload
        if not isinstance(payload, dict):
            msg = "A retained submission finalization payload is not a JSON object."
            raise DataIntegrityError(msg)
        if job.payload_sha256 != submission_payload_digest(payload):
            msg = "A retained submission finalization payload failed its integrity check."
            raise DataIntegrityError(msg)
        owner = payload.get("owner_account_id")
        if not isinstance(owner, int) or isinstance(owner, bool) or owner not in {absorbed, survivor}:
            msg = "A retained submission finalization payload has conflicting account provenance."
            raise DataIntegrityError(msg)
        rewritten = dict(payload)
        rewritten["owner_account_id"] = survivor
        job.payload = rewritten
        job.payload_sha256 = submission_payload_digest(rewritten)
        job.updated_at = rewritten_at
        if job.status is FinalizationJobStatus.CLAIMED:
            job.status = FinalizationJobStatus.PENDING
            job.available_at = rewritten_at
            job.claimed_at = None
            job.claim_token = None
            job.claim_expires_at = None
            job.completed_at = None
            job.attention_at = None
            job.dead_at = None
            job.attention_issues = []
