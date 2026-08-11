"""Isolated tests for the strict CLI authorization HTTP contract."""

import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import AnyHttpUrl
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.errors import register_exception_handlers
from squid.api.security import Principal, current_principal
from squid.api.v1.cli_auth import (
    current_browser_account_id,
    current_cli_identity,
    get_cli_authorization_service,
    router,
)
from squid.api.v1.schemas.cli_auth import CliEnrollmentCreateRequest
from squid.cli_auth.domain import (
    CliDevice,
    CliDeviceEnrollment,
    CliIdentity,
    CliSession,
    CliSessionChallenge,
    IssuedCliEnrollment,
    IssuedCliSession,
    IssuedCliSessionChallenge,
)
from squid.cli_auth.errors import CliAuthorizationPendingError
from squid.idempotency import PendingRequest

pytestmark = pytest.mark.asyncio

ACCOUNT_ID = 42
ENROLLMENT_ID = UUID("b2e8510e-42b6-469e-bf70-2bbedf0507c7")
DEVICE_ID = UUID("ea252a1c-0bcd-47f7-84d8-36e6801eb374")
CHALLENGE_ID = UUID("a41004ce-570f-43fa-b9d0-8c641226b966")
SESSION_ID = UUID("f5f51999-37c1-4a85-9d7e-f53875428f99")
CLIENT_INSTANCE_ID = UUID("cafecafe-cafe-4afe-8afe-cafecafecafe")
NOW = Instant.parse_iso("2026-08-11T22:00:00Z")
PUBLIC_KEY = b"k" * 32
PUBLIC_KEY_TEXT = base64.urlsafe_b64encode(PUBLIC_KEY).rstrip(b"=").decode()
SIGNATURE_TEXT = base64.urlsafe_b64encode(b"s" * 64).rstrip(b"=").decode()
DEVICE_CODE = "d" * 43
USER_CODE = "ABCD-EFGH"
NONCE = "n" * 43
TOKEN = f"squid_cli_v1_{SESSION_ID.hex}_{'t' * 43}"
VERIFICATION_URI = AnyHttpUrl("https://catalogue.test/cli/link")


def enrollment(*, approved: bool = False) -> CliDeviceEnrollment:
    return CliDeviceEnrollment(
        id=ENROLLMENT_ID,
        device_code_hash=b"d" * 32,
        user_code_hash=b"u" * 32,
        public_key=PUBLIC_KEY,
        client_instance_id=CLIENT_INSTANCE_ID,
        label="Test workstation",
        created_at=NOW,
        expires_at=NOW.add(minutes=10),
        approved_by_account_id=ACCOUNT_ID if approved else None,
        approved_at=NOW if approved else None,
    )


def device() -> CliDevice:
    return CliDevice(
        id=DEVICE_ID,
        account_id=ACCOUNT_ID,
        public_key=PUBLIC_KEY,
        client_instance_id=CLIENT_INSTANCE_ID,
        label="Test workstation",
        created_at=NOW,
        last_used_at=NOW,
    )


def issued_session() -> IssuedCliSession:
    return IssuedCliSession(
        device(),
        CliSession(
            id=SESSION_ID,
            device_id=DEVICE_ID,
            token_hash=b"t" * 32,
            issued_at=NOW,
            expires_at=NOW.add(minutes=15),
            last_seen_at=NOW,
        ),
        TOKEN,
    )


class FakeCliAuthorization:
    """Record HTTP-to-application translations and return stable domain values."""

    def __init__(self) -> None:
        self.started: tuple[bytes, UUID, str] | None = None
        self.approved_as: int | None = None
        self.enrollment_signature: bytes | None = None
        self.session_proof: tuple[UUID, UUID, str, bytes] | None = None
        self.revoked_device: tuple[UUID, int] | None = None
        self.revoked_identity: CliIdentity | None = None
        self.pending = False

    async def start_enrollment(
        self,
        *,
        public_key: bytes,
        client_instance_id: UUID,
        label: str,
    ) -> IssuedCliEnrollment:
        self.started = (public_key, client_instance_id, label)
        return IssuedCliEnrollment(enrollment(), DEVICE_CODE, USER_CODE, 3)

    async def preview_enrollment(self, user_code: str) -> CliDeviceEnrollment:
        assert user_code == USER_CODE
        return enrollment()

    async def approve_enrollment(self, *, user_code: str, account_id: int) -> CliDeviceEnrollment:
        assert user_code == USER_CODE
        self.approved_as = account_id
        return enrollment(approved=True)

    async def exchange_enrollment(self, *, device_code: str, signature: bytes) -> IssuedCliSession:
        assert device_code == DEVICE_CODE
        if self.pending:
            raise CliAuthorizationPendingError
        self.enrollment_signature = signature
        return issued_session()

    async def start_session_challenge(self, device_id: UUID) -> IssuedCliSessionChallenge:
        assert device_id == DEVICE_ID
        return IssuedCliSessionChallenge(
            CliSessionChallenge(
                id=CHALLENGE_ID,
                device_id=DEVICE_ID,
                nonce_hash=b"n" * 32,
                created_at=NOW,
                expires_at=NOW.add(minutes=2),
            ),
            NONCE,
        )

    async def exchange_session_challenge(
        self,
        *,
        device_id: UUID,
        challenge_id: UUID,
        nonce: str,
        signature: bytes,
    ) -> IssuedCliSession:
        self.session_proof = (device_id, challenge_id, nonce, signature)
        return issued_session()

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        assert account_id == ACCOUNT_ID
        return (device(),)

    async def revoke_device(self, *, device_id: UUID, account_id: int) -> bool:
        self.revoked_device = (device_id, account_id)
        return True

    async def revoke_current_session(self, identity: CliIdentity) -> bool:
        self.revoked_identity = identity
        return True


class RecordingIdempotency:
    def __init__(self) -> None:
        self.reservations: list[dict[str, object]] = []

    async def reserve(self, **values: object) -> PendingRequest:
        self.reservations.append(values)
        return PendingRequest(uuid4())


def app_with_fake(
    cli: FakeCliAuthorization,
    *,
    idempotency: RecordingIdempotency | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.runtime = SimpleNamespace(services=SimpleNamespace(idempotency=idempotency))
    app.state.config = SimpleNamespace(cli_auth=SimpleNamespace(verification_uri=VERIFICATION_URI))
    register_exception_handlers(app)
    app.include_router(router)

    async def service_dependency() -> FakeCliAuthorization:
        return cli

    async def account_dependency() -> int:
        return ACCOUNT_ID

    async def identity_dependency() -> CliIdentity:
        return CliIdentity(
            account_id=ACCOUNT_ID,
            device_id=DEVICE_ID,
            session_id=SESSION_ID,
            consent_pending=False,
        )

    async def principal_dependency() -> Principal:
        return Principal(kind="account", subject=f"account:{ACCOUNT_ID}", account_id=ACCOUNT_ID)

    app.dependency_overrides[get_cli_authorization_service] = service_dependency
    app.dependency_overrides[current_browser_account_id] = account_dependency
    app.dependency_overrides[current_cli_identity] = identity_dependency
    app.dependency_overrides[current_principal] = principal_dependency
    return app


async def test_request_schema_forbids_client_supplied_account_authority() -> None:
    with pytest.raises(PydanticValidationError):
        CliEnrollmentCreateRequest.model_validate(
            {
                "public_key": PUBLIC_KEY_TEXT,
                "client_instance_id": str(CLIENT_INSTANCE_ID),
                "label": "workstation",
                "account_id": ACCOUNT_ID,
            }
        )


async def test_enrollment_returns_fragment_approval_link_and_no_store() -> None:
    cli = FakeCliAuthorization()
    idempotency = RecordingIdempotency()
    app = app_with_fake(cli, idempotency=idempotency)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:
        response = await client.post(
            "/cli/auth/enrollments",
            json={
                "public_key": PUBLIC_KEY_TEXT,
                "client_instance_id": str(CLIENT_INSTANCE_ID),
                "label": "Test workstation",
            },
            headers={"Idempotency-Key": "enroll-1"},
        )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["verification_uri_complete"] == f"{VERIFICATION_URI}#code=ABCD-EFGH"
    assert cli.started == (PUBLIC_KEY, CLIENT_INSTANCE_ID, "Test workstation")
    assert idempotency.reservations[0]["principal"].startswith("cli-anonymous:")


async def test_browser_previews_fingerprint_before_approval() -> None:
    cli = FakeCliAuthorization()
    app = app_with_fake(cli)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:
        preview = await client.get("/cli/auth/enrollments/approval", params={"user_code": USER_CODE})
        approved = await client.post(
            "/cli/auth/enrollments/approval",
            json={"user_code": USER_CODE},
        )

    assert preview.status_code == 200
    assert preview.json()["public_key_fingerprint"] == "5E31-8F8C-F9CB-E249-A308"
    assert approved.status_code == 200
    assert approved.json()["approved_at"] is not None
    assert cli.approved_as == ACCOUNT_ID


async def test_device_proofs_exchange_for_one_time_session_tokens() -> None:
    cli = FakeCliAuthorization()
    app = app_with_fake(cli)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:
        enrollment_response = await client.post(
            "/cli/auth/enrollments/exchange",
            json={"device_code": DEVICE_CODE, "signature": SIGNATURE_TEXT},
        )
        challenge_response = await client.post(
            "/cli/auth/session-challenges",
            json={"device_id": str(DEVICE_ID)},
        )
        session_response = await client.post(
            "/cli/auth/sessions",
            json={
                "device_id": str(DEVICE_ID),
                "challenge_id": str(CHALLENGE_ID),
                "nonce": NONCE,
                "signature": SIGNATURE_TEXT,
            },
        )

    assert enrollment_response.json()["token"] == TOKEN
    assert challenge_response.json()["nonce"] == NONCE
    assert session_response.json()["token"] == TOKEN
    assert cli.enrollment_signature == b"s" * 64
    assert cli.session_proof == (DEVICE_ID, CHALLENGE_ID, NONCE, b"s" * 64)


async def test_pending_exchange_is_a_stable_conflict() -> None:
    cli = FakeCliAuthorization()
    cli.pending = True
    app = app_with_fake(cli)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:
        response = await client.post(
            "/cli/auth/enrollments/exchange",
            json={"device_code": DEVICE_CODE, "signature": SIGNATURE_TEXT},
        )

    assert response.status_code == 409
    assert response.json()["context"] == {"cli_auth_code": "cli_authorization_pending"}


async def test_account_and_current_session_revocation_are_separately_scoped() -> None:
    cli = FakeCliAuthorization()
    app = app_with_fake(cli)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:
        listed = await client.get("/cli/auth/devices")
        revoked_device = await client.delete(f"/cli/auth/devices/{DEVICE_ID}")
        revoked_session = await client.delete("/cli/auth/sessions/current")

    assert listed.json()["devices"][0]["id"] == str(DEVICE_ID)
    assert revoked_device.status_code == 204
    assert revoked_session.status_code == 204
    assert cli.revoked_device == (DEVICE_ID, ACCOUNT_ID)
    assert cli.revoked_identity == CliIdentity(
        account_id=ACCOUNT_ID,
        device_id=DEVICE_ID,
        session_id=SESSION_ID,
        consent_pending=False,
    )
