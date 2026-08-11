"""Persistence and identity ports for Minecraft authorization."""

from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
    MinecraftClientOrigin,
    MinecraftPlayerContext,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PlayerGrant,
    PublicServerProfile,
    PublishedPaperServer,
)


class AccountIdentityAuthorizer(Protocol):
    """Authoritatively check current consent and verified Java identities."""

    async def has_current_consent(self, account_id: int) -> bool:
        """Return whether the account has accepted the current privacy notice."""
        ...

    async def can_approve(self, *, account_id: int, java_uuid: UUID) -> bool:
        """Return whether the account currently consents and owns the identity."""
        ...


class MinecraftAuthorizationRepository(Protocol):
    """Atomic persistence operations required by Minecraft authorization."""

    async def add_installation(self, installation: PaperInstallation) -> PaperInstallation: ...

    async def get_installation(self, installation_id: UUID) -> PaperInstallation | None: ...

    async def list_installations(self, owner_account_id: int) -> tuple[PaperInstallation, ...]: ...

    async def list_public_servers(self) -> tuple[PublishedPaperServer, ...]: ...

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None: ...

    async def rotate_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        secret_hash: bytes,
        rotated_at: Instant,
    ) -> PaperInstallation | None: ...

    async def revoke_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        revoked_at: Instant,
    ) -> PaperInstallation | None: ...

    async def update_installation_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation | None: ...

    async def add_challenge(
        self,
        challenge: PlayerAuthorizationChallenge,
        *,
        max_active: int,
    ) -> PlayerAuthorizationChallenge: ...

    async def get_challenge_by_user_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None: ...

    async def get_challenge_by_device_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None: ...

    async def approve_challenge(
        self,
        *,
        challenge_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> PlayerAuthorizationChallenge: ...

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
    ) -> PlayerGrant: ...

    async def get_grant(self, grant_id: UUID) -> PlayerGrant | None: ...

    async def revoke_grant(self, *, grant_id: UUID, account_id: int, revoked_at: Instant) -> bool: ...

    async def revoke_account_grants(self, *, account_id: int, revoked_at: Instant) -> int: ...


class MinecraftPlayerTokenAuthenticator(Protocol):
    """Narrow dependency accepted by Minecraft-facing submission routes."""

    async def authenticate_paper_player(
        self,
        token: str,
        installation: AuthenticatedPaperInstallation,
    ) -> MinecraftPlayerContext:
        """Authenticate a player grant on an already-authenticated Paper transport."""
        ...

    async def authenticate_fabric_player(self, token: str) -> MinecraftPlayerContext:
        """Authenticate a player grant on the Fabric transport."""
        ...
