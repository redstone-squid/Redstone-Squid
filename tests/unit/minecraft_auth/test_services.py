"""Security invariants for Minecraft installation and player authorization."""

import base64
import hashlib
from collections.abc import Iterator
from uuid import UUID

import pytest
from whenever import Instant

from squid.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.minecraft_auth.application.services import InstallationCredentialService, PlayerAuthorizationService
from squid.minecraft_auth.domain import MinecraftClientOrigin, PublicServerProfile
from squid.minecraft_auth.errors import (
    AccountConsentRequiredError,
    AuthorizationPendingError,
    ChallengeAlreadyExchangedError,
    ChallengeApprovalDeniedError,
    ChallengeExpiredError,
    InvalidInstallationCredentialError,
    InvalidPkceError,
    InvalidPlayerTokenError,
    TooManyActiveChallengesError,
)
from tests.unit.minecraft_auth.fakes import FakeAccounts, FakeMinecraftAuthorizationRepository

pytestmark = pytest.mark.asyncio

ACCOUNT_ID = 41
OTHER_ACCOUNT_ID = 42
JAVA_UUID = UUID("d8de679a-3de4-4cb9-9f11-c961c72a3531")
INSTALLATION_ID = UUID("a2b0b451-1591-42e0-ad75-165b43409eaf")
CHALLENGE_IDS = (
    UUID("3236a702-9171-4d7e-961c-f34707691cef"),
    UUID("139533d8-3172-4b0f-bb86-c76603cd75af"),
    UUID("1fdb8ca9-b2eb-4ea1-ad37-115aa0a88ebc"),
    UUID("3a0fb1bb-dfa2-4727-8e75-7f8084b3b55e"),
)
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")
PKCE_VERIFIER = "correct-verifier-" + "a" * 27
PKCE_CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(PKCE_VERIFIER.encode()).digest()).rstrip(b"=").decode()


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> Instant:
        return self.current


class ByteSource:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self, size: int) -> bytes:
        self.value += 1
        return bytes([self.value]) * size


def uuid_source(values: tuple[UUID, ...]) -> Iterator[UUID]:
    yield from values


def services(
    *,
    max_active_challenges: int = 5,
) -> tuple[
    FakeMinecraftAuthorizationRepository,
    FakeAccounts,
    Clock,
    InstallationCredentialService,
    PlayerAuthorizationService,
]:
    repository = FakeMinecraftAuthorizationRepository()
    accounts = FakeAccounts()
    accounts.consented.add(ACCOUNT_ID)
    accounts.java_identities[ACCOUNT_ID] = {JAVA_UUID}
    clock = Clock()
    ids = uuid_source((INSTALLATION_ID, *CHALLENGE_IDS))
    codec = MinecraftSecretCodec(b"test-pepper", token_bytes=ByteSource())
    installations = InstallationCredentialService(repository, accounts, codec, now=clock, new_uuid=lambda: next(ids))
    players = PlayerAuthorizationService(
        repository,
        accounts,
        codec,
        now=clock,
        new_uuid=lambda: next(ids),
        max_active_challenges=max_active_challenges,
    )
    return repository, accounts, clock, installations, players


async def test_installation_secret_is_one_time_hash_only_and_never_player_authority() -> None:
    repository, _, _, installations, players = services()

    issued = await installations.register(owner_account_id=ACCOUNT_ID, label=" Private server ")
    authenticated = await installations.authenticate(issued.token)

    assert issued.installation.label == "Private server"
    assert authenticated.id == INSTALLATION_ID
    assert issued.token.encode() not in issued.installation.secret_hash
    assert all(issued.token not in repr(value) for value in repository.installations.values())
    with pytest.raises(InvalidPlayerTokenError):
        await players.authenticate_paper_player(issued.token, authenticated)


async def test_registration_requires_current_consent_and_profile_is_explicit_opt_in() -> None:
    _, accounts, _, installations, _ = services()
    accounts.consented.clear()

    with pytest.raises(AccountConsentRequiredError):
        await installations.register(owner_account_id=ACCOUNT_ID, label="Server")
    with pytest.raises(ValueError, match="Sponsor opt-in"):
        PublicServerProfile(sponsor_opt_in=True)


async def test_public_listing_is_an_explicit_secret_free_projection() -> None:
    _, _, _, installations, _ = services()
    private = await installations.register(owner_account_id=ACCOUNT_ID, label="Private")
    assert await installations.public_servers() == ()

    profile = PublicServerProfile(enabled=True, display_name="Community", sponsor_opt_in=True)
    await installations.update_profile(
        installation_id=private.installation.id,
        owner_account_id=ACCOUNT_ID,
        profile=profile,
    )

    (published,) = await installations.public_servers()
    assert published.installation_id == private.installation.id
    assert published.profile == profile
    assert await installations.get_public_server(private.installation.id) == published
    assert not hasattr(published, "secret_hash")

    await installations.update_profile(
        installation_id=private.installation.id,
        owner_account_id=ACCOUNT_ID,
        profile=PublicServerProfile(enabled=True, display_name="Listed without sponsor consent"),
    )
    assert len(await installations.public_servers()) == 1
    assert await installations.get_public_server(private.installation.id) is None


async def test_rotation_invalidates_old_secret_and_increments_fence() -> None:
    _, _, _, installations, _ = services()
    first = await installations.register(owner_account_id=ACCOUNT_ID, label="Server")

    second = await installations.rotate(installation_id=first.installation.id, owner_account_id=ACCOUNT_ID)

    assert second.installation.id == first.installation.id
    assert second.installation.credential_version == 2
    with pytest.raises(InvalidInstallationCredentialError):
        await installations.authenticate(first.token)
    assert (await installations.authenticate(second.token)).credential_version == 2


async def test_paper_flow_binds_account_uuid_origin_and_installation() -> None:
    repository, _, _, installations, players = services()
    installation_issue = await installations.register(owner_account_id=ACCOUNT_ID, label="Server")
    installation = await installations.authenticate(installation_issue.token)
    challenge = await players.start_paper_challenge(installation=installation, java_uuid=JAVA_UUID)

    with pytest.raises(AuthorizationPendingError):
        await players.exchange_paper(device_code=challenge.device_code, installation=installation)
    await players.approve(user_code=challenge.user_code.lower(), account_id=ACCOUNT_ID)
    issued = await players.exchange_paper(device_code=challenge.device_code, installation=installation)
    context = await players.authenticate_paper_player(issued.token, installation)

    assert context.account_id == ACCOUNT_ID
    assert context.java_uuid == JAVA_UUID
    assert context.origin is MinecraftClientOrigin.PAPER
    assert context.installation_id == installation.id
    assert issued.token.encode() not in repository.grants[issued.grant.id].token_hash
    with pytest.raises(ChallengeAlreadyExchangedError):
        await players.exchange_paper(device_code=challenge.device_code, installation=installation)


async def test_approval_requires_exact_verified_identity_with_current_consent() -> None:
    _, accounts, _, installations, players = services()
    installation_issue = await installations.register(owner_account_id=ACCOUNT_ID, label="Server")
    installation = await installations.authenticate(installation_issue.token)
    challenge = await players.start_paper_challenge(installation=installation, java_uuid=JAVA_UUID)
    accounts.consented.add(OTHER_ACCOUNT_ID)

    with pytest.raises(ChallengeApprovalDeniedError):
        await players.approve(user_code=challenge.user_code, account_id=OTHER_ACCOUNT_ID)
    accounts.consented.remove(ACCOUNT_ID)
    with pytest.raises(ChallengeApprovalDeniedError):
        await players.approve(user_code=challenge.user_code, account_id=ACCOUNT_ID)


async def test_fabric_requires_pkce_and_origin_cannot_be_reasserted() -> None:
    _, _, _, installations, players = services()
    challenge = await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)
    await players.approve(user_code=challenge.user_code, account_id=ACCOUNT_ID)

    with pytest.raises(InvalidPkceError):
        await players.exchange_fabric(device_code=challenge.device_code, pkce_verifier="stolen-device-code")
    issued = await players.exchange_fabric(device_code=challenge.device_code, pkce_verifier=PKCE_VERIFIER)
    context = await players.authenticate_fabric_player(issued.token)

    assert context.origin is MinecraftClientOrigin.FABRIC
    installation_issue = await installations.register(owner_account_id=ACCOUNT_ID, label="Server")
    installation = await installations.authenticate(installation_issue.token)
    with pytest.raises(InvalidPlayerTokenError):
        await players.authenticate_paper_player(issued.token, installation)


async def test_rotating_paper_installation_revokes_bound_grant() -> None:
    _, _, _, installations, players = services()
    first = await installations.register(owner_account_id=ACCOUNT_ID, label="Server")
    authenticated = await installations.authenticate(first.token)
    challenge = await players.start_paper_challenge(installation=authenticated, java_uuid=JAVA_UUID)
    await players.approve(user_code=challenge.user_code, account_id=ACCOUNT_ID)
    grant = await players.exchange_paper(device_code=challenge.device_code, installation=authenticated)

    await installations.rotate(installation_id=authenticated.id, owner_account_id=ACCOUNT_ID)

    with pytest.raises(InvalidPlayerTokenError):
        await players.authenticate_paper_player(grant.token, authenticated)


async def test_active_challenges_are_bounded_per_identity_and_initiator() -> None:
    _, _, _, _, players = services(max_active_challenges=2)

    await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)
    await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)

    with pytest.raises(TooManyActiveChallengesError):
        await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)


async def test_expired_challenge_cannot_be_approved() -> None:
    _, _, clock, _, players = services()
    challenge = await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)
    clock.current = challenge.expires_at

    with pytest.raises(ChallengeExpiredError):
        await players.approve(user_code=challenge.user_code, account_id=ACCOUNT_ID)
