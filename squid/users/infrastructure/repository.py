"""SQLAlchemy user repository."""

import hashlib
import uuid
from collections.abc import Sequence

from advanced_alchemy.exceptions import NotFoundError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.persistence.repository import BaseAsyncRepository
from squid.users.application.ports import VerificationLinkResult
from squid.users.domain import (
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    UserAccount,
    UserConsent,
    normalize_ign,
)
from squid.users.errors import (
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    CreatorAliasNotFoundError,
    UserNotFoundError,
)
from squid.users.infrastructure.models import CreatorAlias as CreatorAliasModel
from squid.users.infrastructure.models import CreatorAliasClaim as CreatorAliasClaimModel
from squid.users.infrastructure.models import User
from squid.users.infrastructure.models import VerificationCode as VerificationCodeModel


class _UserModelRepository(BaseAsyncRepository[User]):
    model_type = User


class _VerificationCodeModelRepository(BaseAsyncRepository[VerificationCodeModel]):
    model_type = VerificationCodeModel


def _to_account(user: User) -> UserAccount:
    consent = None
    if user.consent_version is not None and user.consented_at is not None:
        consent = UserConsent(version=user.consent_version, granted_at=user.consented_at)
    return UserAccount(
        discord_id=user.discord_id,
        minecraft_uuid=user.minecraft_uuid,
        ign=user.ign,
        consent=consent,
        id=user.id,
        created_at=user.created_at,
        public_creator_id=user.public_creator_id,
    )


def _to_alias(alias: CreatorAliasModel, public_creator_id: uuid.UUID | None = None) -> CreatorAlias:
    return CreatorAlias(
        id=alias.id,
        name=alias.name,
        user_id=alias.user_id,
        claimed_at=alias.claimed_at,
        claim_method=alias.claim_method,
        public_creator_id=public_creator_id,
    )


def _to_claim(claim: CreatorAliasClaimModel, alias_name: str) -> AliasClaim:
    return AliasClaim(
        id=claim.id,
        alias_id=claim.alias_id,
        alias_name=alias_name,
        user_id=claim.user_id,
        status=ClaimStatus(claim.status),
        created_at=claim.created_at,
        resolved_at=claim.resolved_at,
        resolved_by_discord_id=claim.resolved_by_discord_id,
    )


class UserRepository:
    """Repository for managing users, creator aliases, and verification codes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], verification_code_pepper: str):
        self._session_factory = session_factory
        self._verification_code_pepper = verification_code_pepper

    async def add(
        self,
        *,
        consent: UserConsent,
        discord_id: int | None = None,
        minecraft_uuid: uuid.UUID | None = None,
        ign: str | None = None,
    ) -> UserAccount:
        """Insert a new account carrying a consent receipt."""
        async with self._session_factory() as session:
            user = User(
                discord_id=discord_id,
                ign=ign,
                minecraft_uuid=minecraft_uuid,
                consent_version=consent.version,
                consented_at=consent.granted_at,
            )
            repository = _UserModelRepository(session=session, auto_commit=True)
            user = await repository.add(user)
            return _to_account(user)

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None:
        """Return the user matching *discord_id* or *None* if not found."""
        async with self._session_factory() as session:
            repository = _UserModelRepository(session=session)
            user = await repository.get_one_or_none(discord_id=discord_id)
            if user is None:
                return None
            return _to_account(user)

    async def get_or_create_discord(self, discord_id: int) -> UserAccount:
        """Return a Discord account, creating a consent-free identity row when absent."""
        async with self._session_factory() as session:
            statement = (
                insert(User)
                .values(discord_id=discord_id)
                .on_conflict_do_update(index_elements=[User.discord_id], set_={"discord_id": discord_id})
                .returning(User)
            )
            user = (await session.scalars(statement)).one()
            await session.commit()
            return _to_account(user)

    async def update(self, user: UserAccount) -> None:
        """Update the Minecraft details for an existing user."""
        if user.discord_id is None:
            msg = "Cannot update a Discord-linked account without a Discord ID."
            raise InvalidStateError(msg, context={"resource": "user"})
        async with self._session_factory() as session:
            repository = _UserModelRepository(session=session, auto_commit=True)
            try:
                stored_user = await repository.get_one(discord_id=user.discord_id)
            except NotFoundError as exc:
                raise UserNotFoundError(user.discord_id) from exc
            stored_user.minecraft_uuid = user.minecraft_uuid
            stored_user.ign = user.ign
            if user.consent is not None:
                stored_user.consent_version = user.consent.version
                stored_user.consented_at = user.consent.granted_at
            await repository.update(stored_user)

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink a user's Minecraft account.

        Args:
            discord_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self._session_factory() as session:
            repository = _UserModelRepository(session=session, auto_commit=True)
            user = await repository.get_one_or_none(discord_id=discord_id)
            if user is None:
                return False
            user.minecraft_uuid = None
            await repository.update(user)
        return True

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        """Return the alias credited under *name*, ignoring case and surrounding space."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CreatorAliasModel, User.public_creator_id)
                    .outerjoin(User, User.id == CreatorAliasModel.user_id)
                    .where(CreatorAliasModel.normalized_name == normalize_ign(name))
                )
            ).one_or_none()
            return None if row is None else _to_alias(row[0], row[1])

    async def get_creator_profile(self, public_id: uuid.UUID) -> CreatorProfile | None:
        """Return the aliases grouped by an opaque public creator identifier."""
        async with self._session_factory() as session:
            user_id = await session.scalar(select(User.id).where(User.public_creator_id == public_id))
            if user_id is None:
                return None
            aliases = (
                await session.execute(
                    select(CreatorAliasModel.name)
                    .where(CreatorAliasModel.user_id == user_id)
                    .order_by(CreatorAliasModel.normalized_name)
                )
            ).scalars()
            return CreatorProfile(public_id=public_id, aliases=tuple(aliases))

    async def claim_unclaimed_alias(self, *, user_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Claim the alias matching *name* only if nobody else holds it.

        The ``user_id IS NULL`` predicate lives in the UPDATE itself, so a
        concurrent claim of the same name cannot be overwritten.
        """
        async with self._session_factory() as session:
            alias = await session.scalar(
                update(CreatorAliasModel)
                .where(
                    CreatorAliasModel.normalized_name == normalize_ign(name),
                    CreatorAliasModel.user_id.is_(None),
                )
                .values(user_id=user_id, claimed_at=Instant.now(), claim_method=method)
                .returning(CreatorAliasModel)
            )
            await session.commit()
            public_id = await session.scalar(select(User.public_creator_id).where(User.id == user_id))
            return None if alias is None else _to_alias(alias, public_id)

    async def request_claim(self, *, name: str, user_id: int) -> AliasClaim:
        """Open a pending claim for *name*, or return the caller's existing pending one."""
        async with self._session_factory() as session:
            alias = await session.scalar(
                select(CreatorAliasModel).where(CreatorAliasModel.normalized_name == normalize_ign(name))
            )
            if alias is None:
                raise CreatorAliasNotFoundError(name)
            if alias.user_id is not None:
                raise AliasAlreadyClaimedError(alias.name)
            existing = await session.scalar(
                select(CreatorAliasClaimModel).where(
                    CreatorAliasClaimModel.alias_id == alias.id,
                    CreatorAliasClaimModel.user_id == user_id,
                    CreatorAliasClaimModel.status == ClaimStatus.PENDING,
                )
            )
            if existing is not None:
                return _to_claim(existing, alias.name)
            claim = CreatorAliasClaimModel(alias_id=alias.id, user_id=user_id)
            session.add(claim)
            await session.commit()
            await session.refresh(claim)
            return _to_claim(claim, alias.name)

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        """Return the claim with *claim_id* or *None* if it does not exist."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CreatorAliasClaimModel, CreatorAliasModel.name)
                    .join(CreatorAliasModel, CreatorAliasModel.id == CreatorAliasClaimModel.alias_id)
                    .where(CreatorAliasClaimModel.id == claim_id)
                )
            ).one_or_none()
            return None if row is None else _to_claim(row[0], row[1])

    async def pending_claims(self) -> Sequence[AliasClaim]:
        """List claims awaiting staff review, oldest first."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(CreatorAliasClaimModel, CreatorAliasModel.name)
                    .join(CreatorAliasModel, CreatorAliasModel.id == CreatorAliasClaimModel.alias_id)
                    .where(CreatorAliasClaimModel.status == ClaimStatus.PENDING)
                    .order_by(CreatorAliasClaimModel.created_at)
                )
            ).all()
            return [_to_claim(claim, name) for claim, name in rows]

    async def resolve_claim(self, *, claim_id: int, status: ClaimStatus, resolved_by_discord_id: int) -> AliasClaim:
        """Approve or reject a pending claim, attaching the alias on approval."""
        async with self._session_factory() as session:
            claim = await session.get(CreatorAliasClaimModel, claim_id)
            if claim is None or claim.status != ClaimStatus.PENDING:
                raise ClaimNotFoundError(claim_id)
            alias = await session.get(CreatorAliasModel, claim.alias_id)
            if alias is None:
                raise ClaimNotFoundError(claim_id)
            if status is ClaimStatus.APPROVED:
                if alias.user_id is not None:
                    raise AliasAlreadyClaimedError(alias.name)
                alias.user_id = claim.user_id
                alias.claimed_at = Instant.now()
                alias.claim_method = ClaimMethod.STAFF_APPROVED
            claim.status = status
            claim.resolved_at = Instant.now()
            claim.resolved_by_discord_id = resolved_by_discord_id
            await session.commit()
            return _to_claim(claim, alias.name)

    def hash_verification_code(self, code: str) -> str:
        """Hash a verification code for storage.

        Verification codes are short, numeric, and short-lived, so a keyed
        SHA-256 digest (rather than a slow password hash) is sufficient to
        keep them non-recoverable from a database dump while remaining cheap
        to verify on every lookup.
        """
        return hashlib.sha256(f"{self._verification_code_pepper}{code}".encode()).hexdigest()

    async def consume_code_and_link_account(
        self,
        *,
        discord_id: int,
        code: str,
        consent: UserConsent,
    ) -> VerificationLinkResult:
        """Consume one code and apply every resulting identity change atomically."""
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

            user = await session.scalar(select(User).where(User.discord_id == discord_id).with_for_update())
            if user is not None and user.minecraft_uuid not in {None, verification_code.minecraft_uuid}:
                return VerificationLinkResult(
                    account=_to_account(user),
                    conflicting_minecraft_uuid=user.minecraft_uuid,
                )

            uuid_holder = await session.scalar(
                select(User).where(User.minecraft_uuid == verification_code.minecraft_uuid).with_for_update()
            )
            if uuid_holder is not None and uuid_holder.discord_id != discord_id:
                return VerificationLinkResult(
                    account=_to_account(user) if user is not None else UserAccount(discord_id, None, None),
                    conflicting_minecraft_uuid=verification_code.minecraft_uuid,
                )

            if user is None:
                user = User(discord_id=discord_id)
                session.add(user)
                await session.flush()
            user.minecraft_uuid = verification_code.minecraft_uuid
            user.ign = verification_code.username
            user.consent_version = consent.version
            user.consented_at = consent.granted_at
            verification_code.valid = False

            alias = await session.scalar(
                update(CreatorAliasModel)
                .where(
                    CreatorAliasModel.normalized_name == normalize_ign(verification_code.username),
                    CreatorAliasModel.user_id.is_(None),
                )
                .values(user_id=user.id, claimed_at=Instant.now(), claim_method=ClaimMethod.VERIFIED_IGN)
                .returning(CreatorAliasModel)
            )
            await session.flush()
            public_id = user.public_creator_id
            return VerificationLinkResult(
                account=_to_account(user),
                claimed_alias=None if alias is None else _to_alias(alias, public_id),
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
                .where(VerificationCodeModel.minecraft_uuid == minecraft_uuid, VerificationCodeModel.valid.is_(True))
                .values(valid=False)
            )
            session.add(
                VerificationCodeModel(
                    minecraft_uuid=minecraft_uuid,
                    code=self.hash_verification_code(code),
                    username=username,
                )
            )
