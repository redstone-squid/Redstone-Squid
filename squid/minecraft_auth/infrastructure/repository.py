"""PostgreSQL repository for Minecraft device authorization."""

import hmac
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.minecraft_auth.domain import MinecraftClientOrigin, PublicServerProfile, PublishedPaperServer
from squid.minecraft_auth.errors import (
    AuthorizationPendingError,
    ChallengeAlreadyExchangedError,
    ChallengeApprovalDeniedError,
    ChallengeExpiredError,
    InvalidChallengeError,
    InvalidInstallationCredentialError,
    TooManyActiveChallengesError,
)
from squid.minecraft_auth.models import PaperInstallation, PlayerAuthorizationChallenge, PlayerGrant
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_key


class PostgresMinecraftAuthorizationRepository:
    """Persist credentials and atomically fence challenge/grant state transitions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add_installation(self, installation: PaperInstallation) -> PaperInstallation:
        """Insert one account-owned Paper installation."""
        async with self._session_factory.begin() as session:
            record = installation
            session.add(record)
            await session.flush()
            return record

    async def get_installation(self, installation_id: UUID) -> PaperInstallation | None:
        """Return one installation without exposing any plaintext credential."""
        async with self._session_factory() as session:
            record = await session.get(PaperInstallation, installation_id)
            return None if record is None else record

    async def list_installations(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        """List one account's installations, including revoked entries for audit."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(PaperInstallation)
                    .where(PaperInstallation.owner_account_id == owner_account_id)
                    .order_by(PaperInstallation.created_at, PaperInstallation.id)
                )
            ).all()
        return tuple(record for record in records)

    async def list_public_servers(self) -> tuple[PublishedPaperServer, ...]:
        """Return explicit, active public profiles without credential or owner internals."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(PaperInstallation)
                    .where(
                        PaperInstallation.public_profile_enabled.is_(True),
                        PaperInstallation.revoked_at.is_(None),
                    )
                    .order_by(PaperInstallation.created_at, PaperInstallation.id)
                )
            ).all()
            return tuple(
                PublishedPaperServer(installation_id=record.id, profile=record.profile, created_at=record.created_at)
                for record in records
            )

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None:
        """Return one sponsor-enabled active public profile without loading the public directory."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PaperInstallation).where(
                    PaperInstallation.id == installation_id,
                    PaperInstallation.public_profile_enabled.is_(True),
                    PaperInstallation.sponsor_opt_in.is_(True),
                    PaperInstallation.revoked_at.is_(None),
                )
            )
        if record is None:
            return None
        return PublishedPaperServer(installation_id=record.id, profile=record.profile, created_at=record.created_at)

    async def rotate_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        secret_hash: bytes,
        rotated_at: Instant,
    ) -> PaperInstallation | None:
        """Replace a secret and revoke every artifact tied to the prior generation."""
        async with self._session_factory.begin() as session:
            record = await self._owned_installation(session, installation_id, owner_account_id)
            if record is None or record.revoked_at is not None:
                return None
            record.secret_hash = secret_hash
            record.credential_version += 1
            record.rotated_at = rotated_at
            await self._fence_installation(session, installation_id, rotated_at)
            await session.flush()
            return record

    async def revoke_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        revoked_at: Instant,
    ) -> PaperInstallation | None:
        """Revoke an owned installation and all authorization derived from it."""
        async with self._session_factory.begin() as session:
            record = await self._owned_installation(session, installation_id, owner_account_id)
            if record is None:
                return None
            if record.revoked_at is None:
                record.revoked_at = revoked_at
                await self._fence_installation(session, installation_id, revoked_at)
                await session.flush()
            return record

    async def update_installation_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation | None:
        """Replace an active installation's opt-in public metadata."""
        async with self._session_factory.begin() as session:
            record = await self._owned_installation(session, installation_id, owner_account_id)
            if record is None or record.revoked_at is not None:
                return None
            record.public_profile_enabled = profile.enabled
            record.public_display_name = profile.display_name
            record.public_address = profile.address
            record.public_description = profile.description
            record.public_website_url = profile.website_url
            record.sponsor_opt_in = profile.sponsor_opt_in
            await session.flush()
            return record

    async def add_challenge(
        self,
        challenge: PlayerAuthorizationChallenge,
        *,
        max_active: int,
    ) -> PlayerAuthorizationChallenge:
        """Insert under a per-identity lock after enforcing the active bound."""
        async with self._session_factory.begin() as session:
            # Counting and inserting are one invariant: without this lock, two
            # requests can both observe room and exceed the per-client bound.
            await lock_key(
                session, _challenge_lock_key(challenge), namespace=AdvisoryLockNamespace.MINECRAFT_ACTIVE_CHALLENGE
            )
            if challenge.origin is MinecraftClientOrigin.PAPER:
                if challenge.installation_id is None or challenge.installation_credential_version is None:
                    raise InvalidInstallationCredentialError
                installation = await session.get(
                    PaperInstallation,
                    challenge.installation_id,
                    with_for_update=True,
                )
                if (
                    installation is None
                    or installation.revoked_at is not None
                    or installation.credential_version != challenge.installation_credential_version
                ):
                    raise InvalidInstallationCredentialError

            conditions = [
                PlayerAuthorizationChallenge.origin == challenge.origin.value,
                PlayerAuthorizationChallenge.java_uuid == challenge.java_uuid,
                PlayerAuthorizationChallenge.expires_at > challenge.created_at,
                PlayerAuthorizationChallenge.exchanged_at.is_(None),
                PlayerAuthorizationChallenge.revoked_at.is_(None),
            ]
            if challenge.installation_id is None:
                conditions.append(PlayerAuthorizationChallenge.installation_id.is_(None))
            else:
                conditions.append(PlayerAuthorizationChallenge.installation_id == challenge.installation_id)
            active = await session.scalar(
                select(func.count()).select_from(PlayerAuthorizationChallenge).where(*conditions)
            )
            if active is not None and active >= max_active:
                raise TooManyActiveChallengesError

            record = challenge
            session.add(record)
            await session.flush()
            return record

    async def get_challenge_by_user_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Resolve a keyed user-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PlayerAuthorizationChallenge).where(PlayerAuthorizationChallenge.user_code_hash == code_hash)
            )
            return None if record is None else record

    async def get_challenge_by_device_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Resolve a keyed device-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PlayerAuthorizationChallenge).where(PlayerAuthorizationChallenge.device_code_hash == code_hash)
            )
            return None if record is None else record

    async def approve_challenge(
        self,
        *,
        challenge_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> PlayerAuthorizationChallenge:
        """Approve one still-live challenge, idempotently for the same account."""
        async with self._session_factory.begin() as session:
            record = await session.get(PlayerAuthorizationChallenge, challenge_id, with_for_update=True)
            if record is None or record.revoked_at is not None:
                raise InvalidChallengeError
            if record.expires_at <= approved_at:
                raise ChallengeExpiredError
            if record.exchanged_at is not None:
                raise ChallengeAlreadyExchangedError
            if record.approved_by_account_id is not None:
                if record.approved_by_account_id != account_id:
                    raise ChallengeApprovalDeniedError
                return record
            record.approved_by_account_id = account_id
            record.approved_at = approved_at
            await session.flush()
            return record

    async def exchange_challenge(
        self,
        *,
        challenge_id: UUID,
        device_code_hash: bytes,
        expected_origin: MinecraftClientOrigin,
        expected_installation_id: UUID | None,
        expected_installation_credential_version: int | None,
        grant: PlayerGrant,
        exchanged_at: Instant,
    ) -> PlayerGrant:
        """Consume one approval and insert its hash-only grant in the same transaction."""
        async with self._session_factory.begin() as session:
            record = await session.get(PlayerAuthorizationChallenge, challenge_id, with_for_update=True)
            if (
                record is None
                or record.revoked_at is not None
                or not hmac.compare_digest(record.device_code_hash, device_code_hash)
                or record.origin != expected_origin.value
                or record.installation_id != expected_installation_id
                or record.installation_credential_version != expected_installation_credential_version
            ):
                raise InvalidChallengeError
            if record.expires_at <= exchanged_at:
                raise ChallengeExpiredError
            if record.exchanged_at is not None:
                raise ChallengeAlreadyExchangedError
            if record.approved_by_account_id is None:
                raise AuthorizationPendingError
            self._validate_grant_matches(record, grant)

            if record.installation_id is not None:
                installation = await session.get(PaperInstallation, record.installation_id, with_for_update=True)
                if (
                    installation is None
                    or installation.revoked_at is not None
                    or installation.credential_version != record.installation_credential_version
                ):
                    raise InvalidChallengeError

            record.exchanged_at = exchanged_at
            grant_record = grant
            session.add(grant_record)
            await session.flush()
            return grant_record

    async def get_grant(self, grant_id: UUID) -> PlayerGrant | None:
        """Return a player grant without its plaintext bearer secret."""
        async with self._session_factory() as session:
            record = await session.get(PlayerGrant, grant_id)
            return None if record is None else record

    async def revoke_grant(self, *, grant_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke one account-owned grant, idempotently."""
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(PlayerGrant)
                .where(PlayerGrant.id == grant_id, PlayerGrant.account_id == account_id)
                .with_for_update()
            )
            if record is None:
                return False
            if record.revoked_at is None:
                record.revoked_at = revoked_at
                await session.flush()
            return True

    async def revoke_account_grants(self, *, account_id: int, revoked_at: Instant) -> int:
        """Revoke all active player grants for one account."""
        async with self._session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(PlayerGrant)
                    .where(PlayerGrant.account_id == account_id, PlayerGrant.revoked_at.is_(None))
                    .values(revoked_at=revoked_at)
                ),
            )
            return int(result.rowcount or 0)

    @staticmethod
    async def _owned_installation(
        session: AsyncSession,
        installation_id: UUID,
        owner_account_id: int,
    ) -> PaperInstallation | None:
        return await session.scalar(
            select(PaperInstallation)
            .where(
                PaperInstallation.id == installation_id,
                PaperInstallation.owner_account_id == owner_account_id,
            )
            .with_for_update()
        )

    @staticmethod
    async def _fence_installation(session: AsyncSession, installation_id: UUID, fenced_at: Instant) -> None:
        await session.execute(
            update(PlayerAuthorizationChallenge)
            .where(
                PlayerAuthorizationChallenge.installation_id == installation_id,
                PlayerAuthorizationChallenge.revoked_at.is_(None),
                PlayerAuthorizationChallenge.exchanged_at.is_(None),
            )
            .values(revoked_at=fenced_at)
        )
        await session.execute(
            update(PlayerGrant)
            .where(
                PlayerGrant.installation_id == installation_id,
                PlayerGrant.revoked_at.is_(None),
            )
            .values(revoked_at=fenced_at)
        )

    @staticmethod
    def _validate_grant_matches(record: PlayerAuthorizationChallenge, grant: PlayerGrant) -> None:
        matches = (
            grant.challenge_id == record.id
            and grant.account_id == record.approved_by_account_id
            and grant.java_uuid == record.java_uuid
            and grant.origin.value == record.origin
            and grant.installation_id == record.installation_id
            and grant.installation_credential_version == record.installation_credential_version
        )
        if not matches:
            msg = "Player grant does not match its approved challenge."
            raise InvalidStateError(
                msg,
                context={"challenge_id": str(record.id), "grant_id": str(grant.id)},
            )


def _challenge_lock_key(challenge: PlayerAuthorizationChallenge) -> bytes:
    return f"{challenge.origin.value}:{challenge.java_uuid}:{challenge.installation_id or '-'}".encode()
