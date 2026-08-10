"""PostgreSQL repository for Minecraft device authorization."""

import hashlib
import hmac
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.minecraft_auth.domain import (
    MinecraftClientOrigin,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PlayerGrant,
    PublicServerProfile,
    PublishedPaperServer,
)
from squid.minecraft_auth.errors import (
    AuthorizationPendingError,
    ChallengeAlreadyExchangedError,
    ChallengeApprovalDeniedError,
    ChallengeExpiredError,
    InvalidChallengeError,
    InvalidInstallationCredentialError,
    TooManyActiveChallengesError,
)
from squid.minecraft_auth.infrastructure.models import (
    PaperInstallationRecord,
    PlayerChallengeRecord,
    PlayerGrantRecord,
)


def _profile(record: PaperInstallationRecord) -> PublicServerProfile:
    return PublicServerProfile(
        enabled=record.public_profile_enabled,
        display_name=record.public_display_name,
        address=record.public_address,
        description=record.public_description,
        website_url=record.public_website_url,
        sponsor_opt_in=record.sponsor_opt_in,
    )


def _installation(record: PaperInstallationRecord) -> PaperInstallation:
    return PaperInstallation(
        id=record.id,
        owner_account_id=record.owner_account_id,
        label=record.label,
        secret_hash=record.secret_hash,
        credential_version=record.credential_version,
        profile=_profile(record),
        created_at=record.created_at,
        rotated_at=record.rotated_at,
        revoked_at=record.revoked_at,
    )


def _challenge(record: PlayerChallengeRecord) -> PlayerAuthorizationChallenge:
    return PlayerAuthorizationChallenge(
        id=record.id,
        device_code_hash=record.device_code_hash,
        user_code_hash=record.user_code_hash,
        origin=MinecraftClientOrigin(record.origin),
        java_uuid=record.java_uuid,
        installation_id=record.installation_id,
        installation_credential_version=record.installation_credential_version,
        pkce_s256_challenge=record.pkce_s256_challenge,
        created_at=record.created_at,
        expires_at=record.expires_at,
        approved_by_account_id=record.approved_by_account_id,
        approved_at=record.approved_at,
        exchanged_at=record.exchanged_at,
        revoked_at=record.revoked_at,
    )


def _grant(record: PlayerGrantRecord) -> PlayerGrant:
    return PlayerGrant(
        id=record.id,
        challenge_id=record.challenge_id,
        token_hash=record.token_hash,
        account_id=record.account_id,
        java_uuid=record.java_uuid,
        origin=MinecraftClientOrigin(record.origin),
        installation_id=record.installation_id,
        installation_credential_version=record.installation_credential_version,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
    )


class PostgresMinecraftAuthorizationRepository:
    """Persist credentials and atomically fence challenge/grant state transitions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add_installation(self, installation: PaperInstallation) -> PaperInstallation:
        """Insert one account-owned Paper installation."""
        async with self._session_factory.begin() as session:
            record = PaperInstallationRecord(
                id=installation.id,
                owner_account_id=installation.owner_account_id,
                label=installation.label,
                secret_hash=installation.secret_hash,
                credential_version=installation.credential_version,
                public_profile_enabled=installation.profile.enabled,
                public_display_name=installation.profile.display_name,
                public_address=installation.profile.address,
                public_description=installation.profile.description,
                public_website_url=installation.profile.website_url,
                sponsor_opt_in=installation.profile.sponsor_opt_in,
                created_at=installation.created_at,
                rotated_at=installation.rotated_at,
                revoked_at=installation.revoked_at,
            )
            session.add(record)
            await session.flush()
            return _installation(record)

    async def get_installation(self, installation_id: UUID) -> PaperInstallation | None:
        """Return one installation without exposing any plaintext credential."""
        async with self._session_factory() as session:
            record = await session.get(PaperInstallationRecord, installation_id)
            return None if record is None else _installation(record)

    async def list_installations(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        """List one account's installations, including revoked entries for audit."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(PaperInstallationRecord)
                    .where(PaperInstallationRecord.owner_account_id == owner_account_id)
                    .order_by(PaperInstallationRecord.created_at, PaperInstallationRecord.id)
                )
            ).all()
        return tuple(_installation(record) for record in records)

    async def list_public_servers(self) -> tuple[PublishedPaperServer, ...]:
        """Return explicit, active public profiles without credential or owner internals."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(PaperInstallationRecord)
                    .where(
                        PaperInstallationRecord.public_profile_enabled.is_(True),
                        PaperInstallationRecord.revoked_at.is_(None),
                    )
                    .order_by(PaperInstallationRecord.created_at, PaperInstallationRecord.id)
                )
            ).all()
            return tuple(
                PublishedPaperServer(installation_id=record.id, profile=_profile(record), created_at=record.created_at)
                for record in records
            )

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
            return _installation(record)

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
            return _installation(record)

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
            return _installation(record)

    async def add_challenge(
        self,
        challenge: PlayerAuthorizationChallenge,
        *,
        max_active: int,
    ) -> PlayerAuthorizationChallenge:
        """Insert under a per-identity lock after enforcing the active bound."""
        async with self._session_factory.begin() as session:
            await session.scalar(select(func.pg_advisory_xact_lock(_challenge_lock_key(challenge))))
            if challenge.origin is MinecraftClientOrigin.PAPER:
                if challenge.installation_id is None or challenge.installation_credential_version is None:
                    raise InvalidInstallationCredentialError
                installation = await session.get(
                    PaperInstallationRecord,
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
                PlayerChallengeRecord.origin == challenge.origin.value,
                PlayerChallengeRecord.java_uuid == challenge.java_uuid,
                PlayerChallengeRecord.expires_at > challenge.created_at,
                PlayerChallengeRecord.exchanged_at.is_(None),
                PlayerChallengeRecord.revoked_at.is_(None),
            ]
            if challenge.installation_id is None:
                conditions.append(PlayerChallengeRecord.installation_id.is_(None))
            else:
                conditions.append(PlayerChallengeRecord.installation_id == challenge.installation_id)
            active = await session.scalar(select(func.count()).select_from(PlayerChallengeRecord).where(*conditions))
            if active is not None and active >= max_active:
                raise TooManyActiveChallengesError

            record = PlayerChallengeRecord(
                id=challenge.id,
                device_code_hash=challenge.device_code_hash,
                user_code_hash=challenge.user_code_hash,
                origin=challenge.origin.value,
                java_uuid=challenge.java_uuid,
                installation_id=challenge.installation_id,
                installation_credential_version=challenge.installation_credential_version,
                pkce_s256_challenge=challenge.pkce_s256_challenge,
                created_at=challenge.created_at,
                expires_at=challenge.expires_at,
                approved_by_account_id=challenge.approved_by_account_id,
                approved_at=challenge.approved_at,
                exchanged_at=challenge.exchanged_at,
                revoked_at=challenge.revoked_at,
            )
            session.add(record)
            await session.flush()
            return _challenge(record)

    async def get_challenge_by_user_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Resolve a keyed user-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PlayerChallengeRecord).where(PlayerChallengeRecord.user_code_hash == code_hash)
            )
            return None if record is None else _challenge(record)

    async def get_challenge_by_device_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Resolve a keyed device-code digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PlayerChallengeRecord).where(PlayerChallengeRecord.device_code_hash == code_hash)
            )
            return None if record is None else _challenge(record)

    async def approve_challenge(
        self,
        *,
        challenge_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> PlayerAuthorizationChallenge:
        """Approve one still-live challenge, idempotently for the same account."""
        async with self._session_factory.begin() as session:
            record = await session.get(PlayerChallengeRecord, challenge_id, with_for_update=True)
            if record is None or record.revoked_at is not None:
                raise InvalidChallengeError
            if record.expires_at <= approved_at:
                raise ChallengeExpiredError
            if record.exchanged_at is not None:
                raise ChallengeAlreadyExchangedError
            if record.approved_by_account_id is not None:
                if record.approved_by_account_id != account_id:
                    raise ChallengeApprovalDeniedError
                return _challenge(record)
            record.approved_by_account_id = account_id
            record.approved_at = approved_at
            await session.flush()
            return _challenge(record)

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
            record = await session.get(PlayerChallengeRecord, challenge_id, with_for_update=True)
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
                installation = await session.get(PaperInstallationRecord, record.installation_id, with_for_update=True)
                if (
                    installation is None
                    or installation.revoked_at is not None
                    or installation.credential_version != record.installation_credential_version
                ):
                    raise InvalidChallengeError

            record.exchanged_at = exchanged_at
            grant_record = PlayerGrantRecord(
                id=grant.id,
                challenge_id=grant.challenge_id,
                token_hash=grant.token_hash,
                account_id=grant.account_id,
                java_uuid=grant.java_uuid,
                origin=grant.origin.value,
                installation_id=grant.installation_id,
                installation_credential_version=grant.installation_credential_version,
                issued_at=grant.issued_at,
                expires_at=grant.expires_at,
                revoked_at=grant.revoked_at,
            )
            session.add(grant_record)
            await session.flush()
            return _grant(grant_record)

    async def get_grant(self, grant_id: UUID) -> PlayerGrant | None:
        """Return a player grant without its plaintext bearer secret."""
        async with self._session_factory() as session:
            record = await session.get(PlayerGrantRecord, grant_id)
            return None if record is None else _grant(record)

    async def revoke_grant(self, *, grant_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke one account-owned grant, idempotently."""
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(PlayerGrantRecord)
                .where(PlayerGrantRecord.id == grant_id, PlayerGrantRecord.account_id == account_id)
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
                    update(PlayerGrantRecord)
                    .where(PlayerGrantRecord.account_id == account_id, PlayerGrantRecord.revoked_at.is_(None))
                    .values(revoked_at=revoked_at)
                ),
            )
            return int(result.rowcount or 0)

    @staticmethod
    async def _owned_installation(
        session: AsyncSession,
        installation_id: UUID,
        owner_account_id: int,
    ) -> PaperInstallationRecord | None:
        return await session.scalar(
            select(PaperInstallationRecord)
            .where(
                PaperInstallationRecord.id == installation_id,
                PaperInstallationRecord.owner_account_id == owner_account_id,
            )
            .with_for_update()
        )

    @staticmethod
    async def _fence_installation(session: AsyncSession, installation_id: UUID, fenced_at: Instant) -> None:
        await session.execute(
            update(PlayerChallengeRecord)
            .where(
                PlayerChallengeRecord.installation_id == installation_id,
                PlayerChallengeRecord.revoked_at.is_(None),
                PlayerChallengeRecord.exchanged_at.is_(None),
            )
            .values(revoked_at=fenced_at)
        )
        await session.execute(
            update(PlayerGrantRecord)
            .where(
                PlayerGrantRecord.installation_id == installation_id,
                PlayerGrantRecord.revoked_at.is_(None),
            )
            .values(revoked_at=fenced_at)
        )

    @staticmethod
    def _validate_grant_matches(record: PlayerChallengeRecord, grant: PlayerGrant) -> None:
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
            raise ValueError(msg)


def _challenge_lock_key(challenge: PlayerAuthorizationChallenge) -> int:
    payload = f"{challenge.origin.value}:{challenge.java_uuid}:{challenge.installation_id or '-'}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], byteorder="big", signed=True)
