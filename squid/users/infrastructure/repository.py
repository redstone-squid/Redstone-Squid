"""SQLAlchemy user repository."""

import hashlib
import uuid

from advanced_alchemy.exceptions import NotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.persistence.repository import BaseAsyncRepository
from squid.users.domain import UserAccount, UserConsent, VerificationCode
from squid.users.errors import UserNotFoundError
from squid.users.infrastructure.models import User
from squid.users.infrastructure.models import VerificationCode as VerificationCodeModel


class _UserModelRepository(BaseAsyncRepository[User]):
    model_type = User


class _VerificationCodeModelRepository(BaseAsyncRepository[VerificationCodeModel]):
    model_type = VerificationCodeModel


class UserRepository:
    """Repository for managing users and verification codes in the database."""

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
        """Insert a new user and return its primary key."""
        async with self._session_factory() as session:
            user = User(
                discord_id=discord_id,
                ign=ign or "",  # FIXME: Allow empty IGN
                minecraft_uuid=minecraft_uuid,
                consent_version=consent.version,
                consented_at=consent.granted_at,
            )
            repository = _UserModelRepository(session=session, auto_commit=True)
            user = await repository.add(user)
            return UserAccount(
                discord_id=user.discord_id,
                minecraft_uuid=user.minecraft_uuid,
                ign=user.ign,
                consent=consent,
            )

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None:
        """Return the user matching *discord_id* or *None* if not found."""
        async with self._session_factory() as session:
            repository = _UserModelRepository(session=session)
            user = await repository.get_one_or_none(discord_id=discord_id)
            if user is None:
                return None
            consent = None
            if user.consent_version is not None and user.consented_at is not None:
                consent = UserConsent(version=user.consent_version, granted_at=user.consented_at)
            return UserAccount(
                discord_id=discord_id, minecraft_uuid=user.minecraft_uuid, ign=user.ign, consent=consent
            )

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
            stored_user.ign = user.ign or ""
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

    def hash_verification_code(self, code: str) -> str:
        """Hash a verification code for storage.

        Verification codes are short, numeric, and short-lived, so a keyed
        SHA-256 digest (rather than a slow password hash) is sufficient to
        keep them non-recoverable from a database dump while remaining cheap
        to verify on every lookup.
        """
        return hashlib.sha256(f"{self._verification_code_pepper}{code}".encode()).hexdigest()

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None:
        """Return a valid verification code matching the given code."""
        async with self._session_factory() as session:
            repository = _VerificationCodeModelRepository(session=session)
            verification_code = await repository.get_one_or_none(
                VerificationCodeModel.expires > Instant.now(),
                code=self.hash_verification_code(code),
                valid=True,
            )
            if verification_code is None:
                return None
            return VerificationCode(
                minecraft_uuid=verification_code.minecraft_uuid,
                username=verification_code.username,
            )

    async def invalidate_codes(self, minecraft_uuid: uuid.UUID) -> None:
        """Invalidate all verification codes for the given Minecraft UUID."""
        async with self._session_factory() as session:
            repository = _VerificationCodeModelRepository(session=session, auto_commit=True)
            verification_codes = await repository.get_many(
                VerificationCodeModel.expires > Instant.now(),
                minecraft_uuid=minecraft_uuid,
                valid=True,
            )
            for verification_code in verification_codes:
                verification_code.valid = False
            if verification_codes:
                await repository.update_many(verification_codes)

    async def create_verification_code(self, *, minecraft_uuid: uuid.UUID, code: str, username: str) -> None:
        """Insert a new verification code for the given Minecraft UUID and username."""
        code = self.hash_verification_code(code)
        async with self._session_factory() as session:
            verification_code = VerificationCodeModel(minecraft_uuid=minecraft_uuid, code=code, username=username)
            repository = _VerificationCodeModelRepository(session=session, auto_commit=True)
            await repository.add(verification_code)
