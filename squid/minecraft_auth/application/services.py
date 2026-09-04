"""Paper installation credentials and player device authorization flows."""

import hmac
from collections.abc import Callable
from uuid import UUID, uuid4

from whenever import Instant

from squid.accounts.application.ports import AccountMinecraftAuthorization
from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.minecraft_auth.application.crypto import (
    MAX_INSTALLATION_SECRET_CHARS,
    MIN_INSTALLATION_SECRET_CHARS,
    MinecraftSecretCodec,
    SecretPurpose,
)
from squid.minecraft_auth.application.ports import MinecraftAuthorizationRepository
from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftClientOrigin,
    MinecraftPlayerContext,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PlayerGrant,
    PublicServerProfile,
    PublishedPaperServer,
)
from squid.minecraft_auth.errors import (
    AccountConsentRequiredError,
    AuthorizationPendingError,
    ChallengeAlreadyExchangedError,
    ChallengeApprovalDeniedError,
    ChallengeExpiredError,
    InstallationUnavailableError,
    InvalidChallengeError,
    InvalidInstallationCredentialError,
    InvalidPkceError,
    InvalidPlayerTokenError,
)

CHALLENGE_LIFETIME_SECONDS = 10 * 60
PLAYER_GRANT_LIFETIME_SECONDS = 5 * 60
POLLING_INTERVAL_SECONDS = 3
MAX_ACTIVE_CHALLENGES = 5


class InstallationCredentialService:
    """Register, rotate, revoke, and authenticate account-owned Paper servers."""

    def __init__(
        self,
        repository: MinecraftAuthorizationRepository,
        accounts: AccountMinecraftAuthorization,
        codec: MinecraftSecretCodec,
        *,
        clock: Callable[[], Instant] = Instant.now,
        new_uuid: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._accounts = accounts
        self._codec = codec
        self._clock = clock
        self._new_uuid = new_uuid

    async def register(
        self,
        *,
        owner_account_id: int,
        label: str,
        profile: PublicServerProfile | None = None,
    ) -> IssuedInstallationCredential:
        """Create an installation and disclose its high-entropy credential once."""
        if not await self._accounts.has_current_consent(owner_account_id):
            raise AccountConsentRequiredError
        normalized_label = _installation_label(label)
        issued_at = self._clock()
        secret = self._codec.random_secret()
        installation = await self._repository.add_installation(
            PaperInstallation(
                id=self._new_uuid(),
                owner_account_id=owner_account_id,
                label=normalized_label,
                secret_hash=self._codec.digest(SecretPurpose.INSTALLATION, secret),
                credential_version=1,
                profile=profile or PublicServerProfile(),
                created_at=issued_at,
            )
        )
        return IssuedInstallationCredential(
            installation=installation,
            token=self._codec.installation_token(installation.id, secret),
        )

    async def rotate(self, *, installation_id: UUID, owner_account_id: int) -> IssuedInstallationCredential:
        """Fence all old credentials and grants, then disclose one replacement secret."""
        secret = self._codec.random_secret()
        installation = await self._repository.rotate_installation(
            installation_id=installation_id,
            owner_account_id=owner_account_id,
            secret_hash=self._codec.digest(SecretPurpose.INSTALLATION, secret),
            rotated_at=self._clock(),
        )
        if installation is None:
            raise InstallationUnavailableError
        return IssuedInstallationCredential(
            installation=installation,
            token=self._codec.installation_token(installation.id, secret),
        )

    async def revoke(self, *, installation_id: UUID, owner_account_id: int) -> PaperInstallation:
        """Revoke an installation and every challenge or grant bound to it."""
        installation = await self._repository.revoke_installation(
            installation_id=installation_id,
            owner_account_id=owner_account_id,
            revoked_at=self._clock(),
        )
        if installation is None:
            raise InstallationUnavailableError
        return installation

    async def list_owned(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        """List an account's installations without exposing credential digests."""
        return await self._repository.list_installations(owner_account_id)

    async def update_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation:
        """Replace explicit public-profile and sponsor preferences without rotating credentials."""
        installation = await self._repository.update_installation_profile(
            installation_id=installation_id,
            owner_account_id=owner_account_id,
            profile=profile,
        )
        if installation is None:
            raise InstallationUnavailableError
        return installation

    async def authenticate(self, token: str) -> AuthenticatedPaperInstallation:
        """Authenticate a Paper server without granting any player authority."""
        parsed = self._codec.parse_installation_token(token)
        if parsed is None:
            raise InvalidInstallationCredentialError
        installation_id, secret = parsed
        installation = await self._repository.get_installation(installation_id)
        now = self._clock()
        if (
            installation is None
            or not installation.is_active_at(now)
            or not hmac.compare_digest(
                installation.secret_hash,
                self._codec.digest(SecretPurpose.INSTALLATION, secret),
            )
        ):
            raise InvalidInstallationCredentialError
        return AuthenticatedPaperInstallation(
            id=installation.id,
            owner_account_id=installation.owner_account_id,
            credential_version=installation.credential_version,
        )

    async def authenticate_headers(
        self,
        installation_id: str | None,
        installation_secret: str | None,
    ) -> AuthenticatedPaperInstallation:
        """Authenticate opaque Paper credential headers as one indistinguishable credential."""
        if installation_id is None or installation_secret is None:
            raise InvalidInstallationCredentialError
        if not MIN_INSTALLATION_SECRET_CHARS <= len(installation_secret) <= MAX_INSTALLATION_SECRET_CHARS:
            raise InvalidInstallationCredentialError
        try:
            parsed_id = UUID(installation_id)
        except ValueError:
            raise InvalidInstallationCredentialError from None
        return await self.authenticate(self._codec.installation_token(parsed_id, installation_secret))

    async def public_servers(self) -> tuple[PublishedPaperServer, ...]:
        """Return only safe projections for installations that explicitly opted into listing."""
        return await self._repository.list_public_servers()

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None:
        """Return one active, public, sponsor-enabled installation without scanning unrelated profiles."""
        return await self._repository.get_public_server(installation_id)


class PlayerAuthorizationService:
    """Approve exact Java identities and issue fenced, origin-bound player grants."""

    def __init__(
        self,
        repository: MinecraftAuthorizationRepository,
        accounts: AccountMinecraftAuthorization,
        codec: MinecraftSecretCodec,
        *,
        clock: Callable[[], Instant] = Instant.now,
        new_uuid: Callable[[], UUID] = uuid4,
        challenge_lifetime_seconds: int = CHALLENGE_LIFETIME_SECONDS,
        grant_lifetime_seconds: int = PLAYER_GRANT_LIFETIME_SECONDS,
        max_active_challenges: int = MAX_ACTIVE_CHALLENGES,
    ) -> None:
        if min(challenge_lifetime_seconds, grant_lifetime_seconds, max_active_challenges) <= 0:
            msg = tr(t"Minecraft authorization limits must be positive.")
            raise InvalidStateError(msg)
        self._repository = repository
        self._accounts = accounts
        self._codec = codec
        self._clock = clock
        self._new_uuid = new_uuid
        self._challenge_lifetime_seconds = challenge_lifetime_seconds
        self._grant_lifetime_seconds = grant_lifetime_seconds
        self._max_active_challenges = max_active_challenges

    async def start_paper_challenge(
        self,
        *,
        installation: AuthenticatedPaperInstallation,
        java_uuid: UUID,
    ) -> IssuedPlayerChallenge:
        """Start a challenge bound to an authenticated Paper credential generation."""
        return await self._start_challenge(
            origin=MinecraftClientOrigin.PAPER,
            java_uuid=java_uuid,
            installation=installation,
            pkce_s256_challenge=None,
        )

    async def start_fabric_challenge(
        self,
        *,
        java_uuid: UUID,
        pkce_s256_challenge: str,
    ) -> IssuedPlayerChallenge:
        """Start a Fabric challenge whose exchange requires an RFC 7636 S256 verifier."""
        challenge = self._codec.validate_s256_challenge(pkce_s256_challenge)
        return await self._start_challenge(
            origin=MinecraftClientOrigin.FABRIC,
            java_uuid=java_uuid,
            installation=None,
            pkce_s256_challenge=challenge,
        )

    async def approve(self, *, user_code: str, account_id: int) -> PlayerAuthorizationChallenge:
        """Approve only when the exact verified Java UUID belongs to a currently consenting account."""
        normalized_code = self._codec.normalize_user_code(user_code)
        if not normalized_code:
            raise InvalidChallengeError
        challenge = await self._repository.get_challenge_by_user_code_hash(
            self._codec.digest(SecretPurpose.USER_CODE, normalized_code)
        )
        if challenge is None:
            raise InvalidChallengeError
        self._ensure_approvable(challenge, self._clock())
        if not await self._accounts.can_approve_minecraft_identity(
            account_id=account_id,
            java_uuid=challenge.java_uuid,
        ):
            raise ChallengeApprovalDeniedError
        return await self._repository.approve_challenge(
            challenge_id=challenge.id,
            account_id=account_id,
            approved_at=self._clock(),
        )

    async def exchange_paper(
        self,
        *,
        device_code: str,
        installation: AuthenticatedPaperInstallation,
    ) -> IssuedPlayerGrant:
        """Exchange an approved Paper challenge on the same authenticated installation generation."""
        challenge = await self._challenge_for_exchange(device_code)
        if (
            challenge.origin is not MinecraftClientOrigin.PAPER
            or challenge.installation_id != installation.id
            or challenge.installation_credential_version != installation.credential_version
        ):
            raise InvalidChallengeError
        return await self._exchange(challenge, device_code)

    async def exchange_fabric(self, *, device_code: str, pkce_verifier: str) -> IssuedPlayerGrant:
        """Exchange an approved Fabric challenge after proving the S256 verifier."""
        challenge = await self._challenge_for_exchange(device_code)
        if (
            challenge.origin is not MinecraftClientOrigin.FABRIC
            or challenge.pkce_s256_challenge is None
            or not self._codec.verify_s256(challenge.pkce_s256_challenge, pkce_verifier)
        ):
            raise InvalidPkceError
        return await self._exchange(challenge, device_code)

    async def authenticate_paper_player(
        self,
        token: str,
        installation: AuthenticatedPaperInstallation,
    ) -> MinecraftPlayerContext:
        """Authenticate a Paper player token only on its bound server credential generation."""
        context, grant = await self._authenticate(token)
        if (
            grant.origin is not MinecraftClientOrigin.PAPER
            or grant.installation_id != installation.id
            or grant.installation_credential_version != installation.credential_version
        ):
            raise InvalidPlayerTokenError
        current_installation = await self._repository.get_installation(installation.id)
        if (
            current_installation is None
            or not current_installation.is_active_at(self._clock())
            or current_installation.credential_version != installation.credential_version
        ):
            raise InvalidPlayerTokenError
        return context

    async def authenticate_fabric_player(self, token: str) -> MinecraftPlayerContext:
        """Authenticate a player token only when it was issued through the Fabric flow."""
        context, grant = await self._authenticate(token)
        if grant.origin is not MinecraftClientOrigin.FABRIC:
            raise InvalidPlayerTokenError
        return context

    async def revoke_grant(self, *, grant_id: UUID, account_id: int) -> bool:
        """Revoke one player grant if it belongs to the signed-in account."""
        return await self._repository.revoke_grant(
            grant_id=grant_id,
            account_id=account_id,
            revoked_at=self._clock(),
        )

    async def revoke_account_grants(self, account_id: int) -> int:
        """Revoke every outstanding Minecraft player grant for an account."""
        return await self._repository.revoke_account_grants(account_id=account_id, revoked_at=self._clock())

    async def _start_challenge(
        self,
        *,
        origin: MinecraftClientOrigin,
        java_uuid: UUID,
        installation: AuthenticatedPaperInstallation | None,
        pkce_s256_challenge: str | None,
    ) -> IssuedPlayerChallenge:
        now = self._clock()
        device_code = self._codec.random_secret()
        user_code = self._codec.random_user_code()
        normalized_user_code = self._codec.normalize_user_code(user_code)
        challenge = await self._repository.add_challenge(
            PlayerAuthorizationChallenge(
                id=self._new_uuid(),
                device_code_hash=self._codec.digest(SecretPurpose.DEVICE_CODE, device_code),
                user_code_hash=self._codec.digest(SecretPurpose.USER_CODE, normalized_user_code),
                origin=origin,
                java_uuid=java_uuid,
                installation_id=None if installation is None else installation.id,
                installation_credential_version=None if installation is None else installation.credential_version,
                pkce_s256_challenge=pkce_s256_challenge,
                created_at=now,
                expires_at=now.add(seconds=self._challenge_lifetime_seconds),
            ),
            max_active=self._max_active_challenges,
        )
        return IssuedPlayerChallenge(
            id=challenge.id,
            device_code=device_code,
            user_code=user_code,
            expires_at=challenge.expires_at,
            polling_interval_seconds=POLLING_INTERVAL_SECONDS,
        )

    async def _challenge_for_exchange(self, device_code: str) -> PlayerAuthorizationChallenge:
        if not device_code:
            raise InvalidChallengeError
        challenge = await self._repository.get_challenge_by_device_code_hash(
            self._codec.digest(SecretPurpose.DEVICE_CODE, device_code)
        )
        if challenge is None:
            raise InvalidChallengeError
        now = self._clock()
        if challenge.revoked_at is not None:
            raise InvalidChallengeError
        if challenge.is_expired_at(now):
            raise ChallengeExpiredError
        if challenge.exchanged_at is not None:
            raise ChallengeAlreadyExchangedError
        if challenge.approved_by_account_id is None:
            raise AuthorizationPendingError
        return challenge

    async def _exchange(
        self,
        challenge: PlayerAuthorizationChallenge,
        device_code: str,
    ) -> IssuedPlayerGrant:
        account_id = challenge.approved_by_account_id
        if account_id is None:
            raise AuthorizationPendingError
        now = self._clock()
        secret = self._codec.random_secret()
        grant = await self._repository.exchange_challenge(
            challenge_id=challenge.id,
            device_code_hash=self._codec.digest(SecretPurpose.DEVICE_CODE, device_code),
            expected_origin=challenge.origin,
            expected_installation_id=challenge.installation_id,
            expected_installation_credential_version=challenge.installation_credential_version,
            grant=PlayerGrant(
                id=self._new_uuid(),
                challenge_id=challenge.id,
                token_hash=self._codec.digest(SecretPurpose.PLAYER_TOKEN, secret),
                account_id=account_id,
                java_uuid=challenge.java_uuid,
                origin=challenge.origin,
                installation_id=challenge.installation_id,
                installation_credential_version=challenge.installation_credential_version,
                issued_at=now,
                expires_at=now.add(seconds=self._grant_lifetime_seconds),
            ),
            exchanged_at=now,
        )
        return IssuedPlayerGrant(grant=grant, token=self._codec.player_token(grant.id, secret))

    async def _authenticate(self, token: str) -> tuple[MinecraftPlayerContext, PlayerGrant]:
        parsed = self._codec.parse_player_token(token)
        if parsed is None:
            raise InvalidPlayerTokenError
        grant_id, secret = parsed
        grant = await self._repository.get_grant(grant_id)
        now = self._clock()
        if (
            grant is None
            or not grant.is_active_at(now)
            or not hmac.compare_digest(grant.token_hash, self._codec.digest(SecretPurpose.PLAYER_TOKEN, secret))
        ):
            raise InvalidPlayerTokenError
        if not await self._accounts.can_approve_minecraft_identity(
            account_id=grant.account_id,
            java_uuid=grant.java_uuid,
        ):
            raise InvalidPlayerTokenError
        return (
            MinecraftPlayerContext(
                grant_id=grant.id,
                account_id=grant.account_id,
                java_uuid=grant.java_uuid,
                origin=grant.origin,
                installation_id=grant.installation_id,
            ),
            grant,
        )

    @staticmethod
    def _ensure_approvable(challenge: PlayerAuthorizationChallenge, now: Instant) -> None:
        if challenge.revoked_at is not None or challenge.exchanged_at is not None:
            raise InvalidChallengeError
        if challenge.is_expired_at(now):
            raise ChallengeExpiredError
        if challenge.approved_by_account_id is not None:
            return


def _installation_label(label: str) -> str:
    normalized = label.strip()
    if not normalized or len(normalized) > 80:
        msg = tr(t"Installation label must contain 1 to 80 characters.")
        raise ValidationError(msg)
    return normalized
