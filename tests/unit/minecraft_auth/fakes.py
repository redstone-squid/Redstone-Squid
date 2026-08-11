"""In-memory collaborators for Minecraft authorization unit tests."""

from dataclasses import replace
from uuid import UUID

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


class FakeAccounts:
    """Authoritative consent and Java-identity fixture."""

    def __init__(self) -> None:
        self.consented: set[int] = set()
        self.java_identities: dict[int, set[UUID]] = {}

    async def has_current_consent(self, account_id: int) -> bool:
        return account_id in self.consented

    async def can_approve(self, *, account_id: int, java_uuid: UUID) -> bool:
        return account_id in self.consented and java_uuid in self.java_identities.get(account_id, set())


class FakeMinecraftAuthorizationRepository:
    """Stateful fake mirroring the repository's atomic transition rules."""

    def __init__(self) -> None:
        self.installations: dict[UUID, PaperInstallation] = {}
        self.challenges: dict[UUID, PlayerAuthorizationChallenge] = {}
        self.grants: dict[UUID, PlayerGrant] = {}

    async def add_installation(self, installation: PaperInstallation) -> PaperInstallation:
        self.installations[installation.id] = installation
        return installation

    async def get_installation(self, installation_id: UUID) -> PaperInstallation | None:
        return self.installations.get(installation_id)

    async def list_installations(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        return tuple(
            installation
            for installation in self.installations.values()
            if installation.owner_account_id == owner_account_id
        )

    async def list_public_servers(self) -> tuple[PublishedPaperServer, ...]:
        return tuple(
            PublishedPaperServer(installation_id=value.id, profile=value.profile, created_at=value.created_at)
            for value in self.installations.values()
            if value.profile.enabled and value.revoked_at is None
        )

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None:
        installation = self.installations.get(installation_id)
        if (
            installation is None
            or not installation.profile.enabled
            or not installation.profile.sponsor_opt_in
            or installation.revoked_at is not None
        ):
            return None
        return PublishedPaperServer(
            installation_id=installation.id,
            profile=installation.profile,
            created_at=installation.created_at,
        )

    async def rotate_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        secret_hash: bytes,
        rotated_at: Instant,
    ) -> PaperInstallation | None:
        installation = self.installations.get(installation_id)
        if (
            installation is None
            or installation.owner_account_id != owner_account_id
            or installation.revoked_at is not None
        ):
            return None
        updated = replace(
            installation,
            secret_hash=secret_hash,
            credential_version=installation.credential_version + 1,
            rotated_at=rotated_at,
        )
        self.installations[installation_id] = updated
        self._fence_installation(installation_id, rotated_at)
        return updated

    async def revoke_installation(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        revoked_at: Instant,
    ) -> PaperInstallation | None:
        installation = self.installations.get(installation_id)
        if installation is None or installation.owner_account_id != owner_account_id:
            return None
        if installation.revoked_at is None:
            installation = replace(installation, revoked_at=revoked_at)
            self.installations[installation_id] = installation
            self._fence_installation(installation_id, revoked_at)
        return installation

    async def update_installation_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation | None:
        installation = self.installations.get(installation_id)
        if (
            installation is None
            or installation.owner_account_id != owner_account_id
            or installation.revoked_at is not None
        ):
            return None
        updated = replace(installation, profile=profile)
        self.installations[installation_id] = updated
        return updated

    async def add_challenge(
        self,
        challenge: PlayerAuthorizationChallenge,
        *,
        max_active: int,
    ) -> PlayerAuthorizationChallenge:
        if challenge.installation_id is not None:
            installation = self.installations.get(challenge.installation_id)
            if (
                installation is None
                or installation.revoked_at is not None
                or installation.credential_version != challenge.installation_credential_version
            ):
                raise InvalidInstallationCredentialError
        active = sum(
            existing.origin is challenge.origin
            and existing.java_uuid == challenge.java_uuid
            and existing.installation_id == challenge.installation_id
            and existing.expires_at > challenge.created_at
            and existing.exchanged_at is None
            and existing.revoked_at is None
            for existing in self.challenges.values()
        )
        if active >= max_active:
            raise TooManyActiveChallengesError
        self.challenges[challenge.id] = challenge
        return challenge

    async def get_challenge_by_user_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        return next(
            (challenge for challenge in self.challenges.values() if challenge.user_code_hash == code_hash), None
        )

    async def get_challenge_by_device_code_hash(self, code_hash: bytes) -> PlayerAuthorizationChallenge | None:
        return next(
            (challenge for challenge in self.challenges.values() if challenge.device_code_hash == code_hash),
            None,
        )

    async def approve_challenge(
        self,
        *,
        challenge_id: UUID,
        account_id: int,
        approved_at: Instant,
    ) -> PlayerAuthorizationChallenge:
        challenge = self.challenges.get(challenge_id)
        if challenge is None or challenge.revoked_at is not None:
            raise InvalidChallengeError
        if challenge.expires_at <= approved_at:
            raise ChallengeExpiredError
        if challenge.exchanged_at is not None:
            raise ChallengeAlreadyExchangedError
        if challenge.approved_by_account_id is not None:
            if challenge.approved_by_account_id != account_id:
                raise ChallengeApprovalDeniedError
            return challenge
        challenge = replace(challenge, approved_by_account_id=account_id, approved_at=approved_at)
        self.challenges[challenge.id] = challenge
        return challenge

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
        challenge = self.challenges.get(challenge_id)
        if (
            challenge is None
            or challenge.revoked_at is not None
            or challenge.device_code_hash != device_code_hash
            or challenge.origin is not expected_origin
            or challenge.installation_id != expected_installation_id
            or challenge.installation_credential_version != expected_installation_credential_version
        ):
            raise InvalidChallengeError
        if challenge.expires_at <= exchanged_at:
            raise ChallengeExpiredError
        if challenge.exchanged_at is not None:
            raise ChallengeAlreadyExchangedError
        if challenge.approved_by_account_id is None:
            raise AuthorizationPendingError
        if challenge.installation_id is not None:
            installation = self.installations[challenge.installation_id]
            if (
                installation.revoked_at is not None
                or installation.credential_version != challenge.installation_credential_version
            ):
                raise InvalidChallengeError
        self.challenges[challenge.id] = replace(challenge, exchanged_at=exchanged_at)
        self.grants[grant.id] = grant
        return grant

    async def get_grant(self, grant_id: UUID) -> PlayerGrant | None:
        return self.grants.get(grant_id)

    async def revoke_grant(self, *, grant_id: UUID, account_id: int, revoked_at: Instant) -> bool:
        grant = self.grants.get(grant_id)
        if grant is None or grant.account_id != account_id:
            return False
        self.grants[grant_id] = replace(grant, revoked_at=grant.revoked_at or revoked_at)
        return True

    async def revoke_account_grants(self, *, account_id: int, revoked_at: Instant) -> int:
        count = 0
        for grant_id, grant in tuple(self.grants.items()):
            if grant.account_id == account_id and grant.revoked_at is None:
                self.grants[grant_id] = replace(grant, revoked_at=revoked_at)
                count += 1
        return count

    def _fence_installation(self, installation_id: UUID, fenced_at: Instant) -> None:
        for challenge_id, challenge in tuple(self.challenges.items()):
            if challenge.installation_id == installation_id and challenge.revoked_at is None:
                self.challenges[challenge_id] = replace(challenge, revoked_at=fenced_at)
        for grant_id, grant in tuple(self.grants.items()):
            if grant.installation_id == installation_id and grant.revoked_at is None:
                self.grants[grant_id] = replace(grant, revoked_at=fenced_at)
