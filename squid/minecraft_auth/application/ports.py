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

    async def add_installation(self, installation: PaperInstallation) -> PaperInstallation:
        """Insert one account-owned Paper installation as stored."""
        ...

    async def get_installation(self, installation_id: UUID) -> PaperInstallation | None:
        """Return one installation, revoked ones included."""
        ...

    async def list_installations(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        """List one account's installations, revoked ones included, oldest first."""
        ...

    async def list_public_servers(self) -> tuple[PublishedPaperServer, ...]:
        """List installations whose owner enabled a public profile and that are not revoked."""
        ...

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None:
        """Return one public profile, and only if its owner also opted into sponsorship."""
        ...

    async def rotate_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        secret_hash: bytes,
        rotated_at: Instant,
    ) -> PaperInstallation | None:
        """Replace the secret, bump the credential version, and revoke what the old one authorized.

        Returns ``None`` when the installation is unknown, owned by another account, or revoked.
        """
        ...

    async def revoke_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        revoked_at: Instant,
    ) -> PaperInstallation | None:
        """Revoke an owned installation and every challenge and grant derived from it.

        Idempotent; returns ``None`` when the installation is unknown or owned by another account.
        """
        ...

    async def update_installation_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation | None:
        """Replace an active installation's opt-in public metadata.

        Returns ``None`` when the installation is unknown, owned by another account, or revoked.
        """
        ...

    async def add_challenge(
        self,
        challenge: PlayerAuthorizationChallenge,
        *,
        max_active: int,
    ) -> PlayerAuthorizationChallenge:
        """Insert a challenge, serialized per origin, Java identity and installation.

        Raises `InvalidInstallationCredentialError` when a Paper challenge names no installation or
        one that is revoked or on another credential version, and `TooManyActiveChallengesError`
        past *max_active* live unexchanged challenges for that identity.
        """
        ...

    async def get_challenge_by_user_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Return the challenge holding this user-code digest, expired or spent ones included."""
        ...

    async def get_challenge_by_device_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        """Return the challenge holding this device-code digest, expired or spent ones included."""
        ...

    async def approve_challenge(
        self,
        *,
        challenge_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> PlayerAuthorizationChallenge:
        """Record approval, idempotently for the account that already approved it.

        Raises:
            InvalidChallengeError: If the challenge is unknown or revoked.
            ChallengeExpiredError: If it expired before *approved_at*.
            ChallengeAlreadyExchangedError: If it was already exchanged.
            ChallengeApprovalDeniedError: If another account approved it first.
        """
        ...

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
        """Spend one approval and insert its digest-only grant in the same transaction.

        Raises:
            InvalidChallengeError: If the challenge is unknown or revoked, if the device code,
                origin or installation do not match what is expected, or if a named installation
                has since been revoked or rotated.
            ChallengeExpiredError: If it expired before *exchanged_at*.
            ChallengeAlreadyExchangedError: If it was already spent.
            AuthorizationPendingError: If nobody has approved it yet.
            ValueError: If *grant* does not carry the challenge's own account, identity and origin.
        """
        ...

    async def get_grant(self, grant_id: UUID) -> PlayerGrant | None:
        """Return one grant, expired and revoked ones included."""
        ...

    async def revoke_grant(self, *, grant_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        """Revoke one account-owned grant, idempotently; return whether that pair exists."""
        ...

    async def revoke_account_grants(self, *, account_id: int, revoked_at: Instant) -> int:
        """Revoke every live grant of one account and return how many were revoked."""
        ...


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
