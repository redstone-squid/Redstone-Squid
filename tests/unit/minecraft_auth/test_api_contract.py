"""Isolated tests for the strict Minecraft authorization HTTP contract."""

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import AnyHttpUrl
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.accounts.errors import ConsentRequiredError
from squid.api.errors import register_exception_handlers
from squid.api.security import ANONYMOUS, Caller, current_caller
from squid.api.v1.minecraft_auth import (
    current_account_id,
    get_installation_service,
    get_player_authorization_service,
    router,
)
from squid.api.v1.schemas.minecraft_auth import (
    ChallengeApprovalRequest,
    FabricChallengeCreateRequest,
    InstallationCreateRequest,
    PaperChallengeCreateRequest,
)
from squid.core.errors import AuthenticationError
from squid.idempotency import IdempotencyService, PendingRequest
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftClientOrigin,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PlayerGrant,
    PublicServerProfile,
)
from squid.minecraft_auth.errors import AuthorizationPendingError
from tests.unit.api.fakes import TEST_CONFIG

pytestmark = pytest.mark.asyncio

ACCOUNT_ID = 42
JAVA_UUID = UUID("d8de679a-3de4-4cb9-9f11-c961c72a3531")
INSTALLATION_ID = UUID("a2b0b451-1591-42e0-ad75-165b43409eaf")
CHALLENGE_ID = UUID("3236a702-9171-4d7e-961c-f34707691cef")
GRANT_ID = UUID("139533d8-3172-4b0f-bb86-c76603cd75af")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")
INSTALLATION_SECRET = "s" * 43
ROTATED_SECRET = "r" * 43
DEVICE_CODE = "d" * 43
USER_CODE = "ABCD-EFGH-IJKL-MNOP"
PKCE_CHALLENGE = "A" * 43
PKCE_VERIFIER = "v" * 43
VERIFICATION_URI = AnyHttpUrl("https://catalogue.test/minecraft/link")


def installation(*, profile: PublicServerProfile | None = None, credential_version: int = 1) -> PaperInstallation:
    return PaperInstallation(
        id=INSTALLATION_ID,
        owner_account_id=ACCOUNT_ID,
        label="Test server",
        secret_hash=b"h" * 32,
        credential_version=credential_version,
        profile=profile or PublicServerProfile(),
        created_at=NOW,
    )


def challenge(*, origin: MinecraftClientOrigin) -> IssuedPlayerChallenge:
    return IssuedPlayerChallenge(
        id=CHALLENGE_ID,
        device_code=DEVICE_CODE,
        user_code=USER_CODE,
        expires_at=NOW.add(minutes=10),
        polling_interval_seconds=3,
    )


def grant(*, origin: MinecraftClientOrigin, installation_id: UUID | None = None) -> IssuedPlayerGrant:
    stored = PlayerGrant(
        id=GRANT_ID,
        challenge_id=CHALLENGE_ID,
        token_hash=b"t" * 32,
        account_id=ACCOUNT_ID,
        java_uuid=JAVA_UUID,
        origin=origin,
        installation_id=installation_id,
        installation_credential_version=1 if installation_id is not None else None,
        issued_at=NOW,
        expires_at=NOW.add(minutes=5),
    )
    return IssuedPlayerGrant(grant=stored, token=f"sqpt_{GRANT_ID.hex}_{'p' * 43}")


class FakeInstallations(InstallationCredentialService):
    def __init__(self) -> None:
        self.current = installation()
        self.authenticated_token: str | None = None
        self.owner_ids: list[int] = []

    async def register(
        self,
        *,
        owner_account_id: int,
        label: str,
        profile: PublicServerProfile | None = None,
    ) -> IssuedInstallationCredential:
        self.owner_ids.append(owner_account_id)
        self.current = replace(self.current, label=label.strip(), profile=profile or PublicServerProfile())
        return IssuedInstallationCredential(
            self.current,
            f"sqpi_{self.current.id.hex}_{INSTALLATION_SECRET}",
        )

    async def list_owned(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        self.owner_ids.append(owner_account_id)
        return (self.current,)

    async def rotate(self, *, installation_id: UUID, owner_account_id: int) -> IssuedInstallationCredential:
        assert installation_id == self.current.id
        self.owner_ids.append(owner_account_id)
        self.current = replace(self.current, credential_version=2, rotated_at=NOW)
        return IssuedInstallationCredential(self.current, f"sqpi_{self.current.id.hex}_{ROTATED_SECRET}")

    async def revoke(self, *, installation_id: UUID, owner_account_id: int) -> PaperInstallation:
        assert installation_id == self.current.id
        self.owner_ids.append(owner_account_id)
        self.current = replace(self.current, revoked_at=NOW)
        return self.current

    async def update_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation:
        assert installation_id == self.current.id
        self.owner_ids.append(owner_account_id)
        self.current = replace(self.current, profile=profile)
        return self.current

    async def authenticate(self, token: str) -> AuthenticatedPaperInstallation:
        self.authenticated_token = token
        return AuthenticatedPaperInstallation(
            id=self.current.id,
            owner_account_id=self.current.owner_account_id,
            credential_version=self.current.credential_version,
        )


class FakePlayers(PlayerAuthorizationService):
    def __init__(self) -> None:
        self.paper_installation: AuthenticatedPaperInstallation | None = None
        self.fabric_proof: tuple[UUID, str] | None = None
        self.approved_as: int | None = None
        self.revoked_as: tuple[UUID, int] | None = None
        self.fabric_error: Exception | None = None

    async def start_paper_challenge(
        self,
        *,
        installation: AuthenticatedPaperInstallation,
        java_uuid: UUID,
    ) -> IssuedPlayerChallenge:
        assert java_uuid == JAVA_UUID
        self.paper_installation = installation
        return challenge(origin=MinecraftClientOrigin.PAPER)

    async def start_fabric_challenge(
        self,
        *,
        java_uuid: UUID,
        pkce_s256_challenge: str,
    ) -> IssuedPlayerChallenge:
        self.fabric_proof = (java_uuid, pkce_s256_challenge)
        return challenge(origin=MinecraftClientOrigin.FABRIC)

    async def approve(self, *, user_code: str, account_id: int) -> PlayerAuthorizationChallenge:
        assert user_code == USER_CODE
        self.approved_as = account_id
        return PlayerAuthorizationChallenge(
            id=CHALLENGE_ID,
            device_code_hash=b"d" * 32,
            user_code_hash=b"u" * 32,
            origin=MinecraftClientOrigin.FABRIC,
            java_uuid=JAVA_UUID,
            created_at=NOW,
            expires_at=NOW.add(minutes=10),
            pkce_s256_challenge=PKCE_CHALLENGE,
            approved_by_account_id=account_id,
            approved_at=NOW,
        )

    async def exchange_paper(
        self,
        *,
        device_code: str,
        installation: AuthenticatedPaperInstallation,
    ) -> IssuedPlayerGrant:
        assert device_code == DEVICE_CODE
        self.paper_installation = installation
        return grant(origin=MinecraftClientOrigin.PAPER, installation_id=installation.id)

    async def exchange_fabric(self, *, device_code: str, pkce_verifier: str) -> IssuedPlayerGrant:
        if self.fabric_error is not None:
            raise self.fabric_error
        assert (device_code, pkce_verifier) == (DEVICE_CODE, PKCE_VERIFIER)
        return grant(origin=MinecraftClientOrigin.FABRIC)

    async def revoke_grant(self, *, grant_id: UUID, account_id: int) -> bool:
        self.revoked_as = (grant_id, account_id)
        return True


class RecordingIdempotency:
    def __init__(self) -> None:
        self.reservations: list[dict[str, object]] = []

    async def reserve(self, **values: object) -> PendingRequest:
        self.reservations.append(values)
        return PendingRequest(uuid4())


def app_with_fakes(
    installations: FakeInstallations,
    players: FakePlayers,
    *,
    signed_in: bool = True,
    idempotency: RecordingIdempotency | None = None,
) -> FastAPI:
    @dataclass(frozen=True, slots=True)
    class Services:
        idempotency: IdempotencyService | RecordingIdempotency | None

    @dataclass(frozen=True, slots=True)
    class Runtime:
        services: Services

    app = FastAPI()
    app.state.runtime = Runtime(Services(idempotency))
    app.state.config = TEST_CONFIG.model_copy(
        update={"minecraft_auth": TEST_CONFIG.minecraft_auth.model_copy(update={"verification_uri": VERIFICATION_URI})}
    )
    register_exception_handlers(app)
    app.include_router(router)

    async def installation_dependency() -> FakeInstallations:
        return installations

    async def player_dependency() -> FakePlayers:
        return players

    async def account_dependency() -> int:
        if not signed_in:
            raise AuthenticationError
        return ACCOUNT_ID

    async def caller_dependency() -> Caller:
        return Caller(kind="account", subject=f"account:{ACCOUNT_ID}", account_id=ACCOUNT_ID)

    app.dependency_overrides[get_installation_service] = installation_dependency
    app.dependency_overrides[get_player_authorization_service] = player_dependency
    app.dependency_overrides[current_account_id] = account_dependency
    app.dependency_overrides[current_caller] = caller_dependency
    return app


async def test_request_schemas_forbid_client_authority_and_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        InstallationCreateRequest.model_validate({"label": "Server", "account_id": ACCOUNT_ID})
    with pytest.raises(PydanticValidationError):
        PaperChallengeCreateRequest.model_validate({"java_uuid": str(JAVA_UUID), "origin": "paper"})
    with pytest.raises(PydanticValidationError):
        FabricChallengeCreateRequest.model_validate(
            {
                "java_uuid": str(JAVA_UUID),
                "pkce_s256_challenge": PKCE_CHALLENGE,
                "origin": "fabric",
            }
        )
    with pytest.raises(PydanticValidationError):
        ChallengeApprovalRequest.model_validate({"user_code": USER_CODE, "account_id": ACCOUNT_ID})


async def test_openapi_declares_paper_headers_and_server_scoped_idempotency() -> None:
    contract = app_with_fakes(FakeInstallations(), FakePlayers()).openapi()
    paths = contract["paths"]
    paper_start = paths["/minecraft/auth/paper/challenges"]["post"]
    header_names = {parameter["name"] for parameter in paper_start["parameters"] if parameter["in"] == "header"}

    assert {"Squid-Installation-ID", "Squid-Installation-Secret"} <= header_names
    assert "Idempotency-Key" in header_names
    mutations = (
        ("/minecraft/auth/paper/installations", "post"),
        ("/minecraft/auth/paper/installations/{installation_id}/rotate", "post"),
        ("/minecraft/auth/paper/installations/{installation_id}/profile", "put"),
        ("/minecraft/auth/paper/installations/{installation_id}", "delete"),
        ("/minecraft/auth/paper/challenges", "post"),
        ("/minecraft/auth/paper/challenges/exchange", "post"),
        ("/minecraft/auth/fabric/challenges", "post"),
        ("/minecraft/auth/fabric/challenges/exchange", "post"),
        ("/minecraft/auth/challenges/approval", "post"),
        ("/minecraft/auth/grants/{grant_id}", "delete"),
    )
    for path, method in mutations:
        operation = paths[path][method]
        assert any(
            parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
            for parameter in operation.get("parameters", [])
        ), f"{method.upper()} {path} lacks Idempotency-Key"


async def test_routes_derive_account_origin_and_paper_installation_from_dependencies() -> None:
    installations = FakeInstallations()
    players = FakePlayers()
    app = app_with_fakes(installations, players)
    paper_headers = {
        "Squid-Installation-ID": str(INSTALLATION_ID),
        "Squid-Installation-Secret": INSTALLATION_SECRET,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/minecraft/auth/paper/installations",
            json={
                "label": " Test server ",
                "profile": {"enabled": True, "display_name": "Community", "sponsor_opt_in": True},
            },
        )
        listed = await client.get("/minecraft/auth/paper/installations")
        profiled = await client.put(
            f"/minecraft/auth/paper/installations/{INSTALLATION_ID}/profile",
            json={"enabled": True, "display_name": "Updated", "sponsor_opt_in": True},
        )
        rotated = await client.post(f"/minecraft/auth/paper/installations/{INSTALLATION_ID}/rotate")
        paper_started = await client.post(
            "/minecraft/auth/paper/challenges",
            headers=paper_headers,
            json={"java_uuid": str(JAVA_UUID)},
        )
        paper_exchanged = await client.post(
            "/minecraft/auth/paper/challenges/exchange",
            headers=paper_headers,
            json={"device_code": DEVICE_CODE},
        )
        fabric_started = await client.post(
            "/minecraft/auth/fabric/challenges",
            json={"java_uuid": str(JAVA_UUID), "pkce_s256_challenge": PKCE_CHALLENGE},
        )
        fabric_exchanged = await client.post(
            "/minecraft/auth/fabric/challenges/exchange",
            json={"device_code": DEVICE_CODE, "pkce_verifier": PKCE_VERIFIER},
        )
        approved = await client.post(
            "/minecraft/auth/challenges/approval",
            json={"user_code": USER_CODE},
        )
        grant_revoked = await client.delete(f"/minecraft/auth/grants/{GRANT_ID}")
        installation_revoked = await client.delete(f"/minecraft/auth/paper/installations/{INSTALLATION_ID}")

    assert created.status_code == 201
    assert created.json()["secret"] == INSTALLATION_SECRET
    assert "secret_hash" not in created.text
    assert listed.json()["installations"][0].keys().isdisjoint({"secret", "secret_hash", "owner_account_id"})
    assert profiled.json()["profile"]["display_name"] == "Updated"
    assert rotated.json()["secret"] == ROTATED_SECRET
    assert paper_started.status_code == 201
    assert paper_started.json()["verification_uri"] == str(VERIFICATION_URI)
    assert paper_started.json()["verification_uri_complete"] == f"{VERIFICATION_URI}?code={USER_CODE}"
    assert paper_exchanged.json()["origin"] == "paper"
    assert fabric_started.status_code == 201
    assert fabric_started.json()["verification_uri_complete"] == f"{VERIFICATION_URI}?code={USER_CODE}"
    assert fabric_exchanged.json()["origin"] == "fabric"
    assert approved.json()["java_uuid"] == str(JAVA_UUID)
    assert grant_revoked.status_code == 204
    assert installation_revoked.status_code == 204
    assert installations.owner_ids == [ACCOUNT_ID] * 5
    assert installations.authenticated_token == f"sqpi_{INSTALLATION_ID.hex}_{INSTALLATION_SECRET}"
    assert players.paper_installation is not None
    assert players.fabric_proof == (JAVA_UUID, PKCE_CHALLENGE)
    assert players.approved_as == ACCOUNT_ID
    assert players.revoked_as == (GRANT_ID, ACCOUNT_ID)
    for response in (
        created,
        listed,
        profiled,
        rotated,
        paper_started,
        paper_exchanged,
        fabric_started,
        fabric_exchanged,
        approved,
        grant_revoked,
        installation_revoked,
    ):
        assert response.headers["Cache-Control"] == "no-store"


async def test_one_time_device_responses_use_server_derived_idempotency_namespaces() -> None:
    idempotency = RecordingIdempotency()
    app = app_with_fakes(FakeInstallations(), FakePlayers(), idempotency=idempotency)
    paper_headers = {
        "Squid-Installation-ID": str(INSTALLATION_ID),
        "Squid-Installation-Secret": INSTALLATION_SECRET,
        "Idempotency-Key": "paper-retry-key",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        paper = await client.post(
            "/minecraft/auth/paper/challenges",
            headers=paper_headers,
            json={"java_uuid": str(JAVA_UUID)},
        )
        fabric = await client.post(
            "/minecraft/auth/fabric/challenges",
            headers={"Idempotency-Key": "fabric-retry-key"},
            json={"java_uuid": str(JAVA_UUID), "pkce_s256_challenge": PKCE_CHALLENGE},
        )

    assert paper.status_code == 201
    assert fabric.status_code == 201
    assert idempotency.reservations[0]["caller"] == f"minecraft-installation:{INSTALLATION_ID}:1"
    fabric_caller = idempotency.reservations[1]["caller"]
    assert isinstance(fabric_caller, str)
    assert fabric_caller.startswith("minecraft-fabric:")
    assert "127.0.0.1" not in fabric_caller


async def test_paper_endpoints_require_both_installation_headers() -> None:
    app = app_with_fakes(FakeInstallations(), FakePlayers())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/minecraft/auth/paper/challenges",
            json={"java_uuid": str(JAVA_UUID)},
        )

    assert response.status_code == 401


async def test_authorization_errors_map_to_stable_problem_context_without_secrets() -> None:
    installations = FakeInstallations()
    players = FakePlayers()
    players.fabric_error = AuthorizationPendingError()
    app = app_with_fakes(installations, players)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/minecraft/auth/fabric/challenges/exchange",
            json={"device_code": DEVICE_CODE, "pkce_verifier": PKCE_VERIFIER},
        )

    assert response.status_code == 409
    assert response.json()["context"] == {"minecraft_auth_code": "authorization_pending"}
    assert DEVICE_CODE not in response.text
    assert PKCE_VERIFIER not in response.text


async def test_account_dependency_rejects_services_and_stale_consent() -> None:
    with pytest.raises(AuthenticationError):
        await current_account_id(ANONYMOUS)
    caller = Caller(
        kind="account",
        subject="account:42",
        account_id=ACCOUNT_ID,
        consent_pending=True,
    )

    with pytest.raises(ConsentRequiredError):
        await current_account_id(caller)
