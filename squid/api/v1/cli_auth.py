"""Approve a CLI device in the browser, then issue sessions from its key proofs.

Enrollment gives the CLI a device code and the user a code to approve in their
signed-in browser. The CLI exchanges that approval and a signature for its first
session. Later sessions use a fresh signed nonce from the enrolled device,
without repeating browser approval. Device revocation invalidates its sessions.
"""

import hashlib
from typing import Annotated, Protocol, cast
from uuid import UUID

from litestar import Request, Router, delete, get, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, JSONBody, QueryParameter, SkipValidation
from pydantic import AnyHttpUrl

from squid.api.contract import ANONYMOUS, DEVICE, WEB, WEB_WRITE, browser_only, cli_command, contract, transport_only
from squid.api.errors import responses
from squid.api.idempotency import IdempotencyKey, reserve_idempotent_request, reserve_idempotent_request_for
from squid.api.security import BrowserAccountId, Caller, current_browser_account_id, prevent_credential_storage
from squid.api.v1.schemas.cli_auth import (
    CliDeviceListResponse,
    CliDeviceResponse,
    CliEnrollmentApprovalRequest,
    CliEnrollmentApprovalResponse,
    CliEnrollmentCreateRequest,
    CliEnrollmentExchangeRequest,
    CliEnrollmentResponse,
    CliSessionChallengeRequest,
    CliSessionChallengeResponse,
    CliSessionExchangeRequest,
    IssuedCliSessionResponse,
    UserCode,
)
from squid.cli_auth.domain import CliIdentity, IssuedCliEnrollment, IssuedCliSession, IssuedCliSessionChallenge
from squid.cli_auth.models import CliDevice, CliDeviceEnrollment
from squid.core.errors import AuthenticationError, NotFoundError, ServiceUnavailableError


class CliAuthorizationHttpService(Protocol):
    """CLI authorization operations consumed by the HTTP transport."""

    async def start_enrollment(
        self,
        *,
        public_key: bytes,
        client_instance_id: UUID,
        label: str,
    ) -> IssuedCliEnrollment: ...

    async def preview_enrollment(self, user_code: str) -> CliDeviceEnrollment: ...

    async def approve_enrollment(self, *, user_code: str, account_id: int) -> CliDeviceEnrollment: ...

    async def exchange_enrollment(self, *, device_code: str, signature: bytes) -> IssuedCliSession: ...

    async def start_session_challenge(self, device_id: UUID) -> IssuedCliSessionChallenge: ...

    async def exchange_session_challenge(
        self,
        *,
        device_id: UUID,
        challenge_id: UUID,
        nonce: str,
        signature: bytes,
    ) -> IssuedCliSession: ...

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]: ...

    async def revoke_device(self, *, device_id: UUID, account_id: int) -> bool: ...

    async def revoke_current_session(self, identity: CliIdentity) -> bool: ...


class CliAuthApiServices(Protocol):
    """Narrow runtime bundle required by CLI authorization routes."""

    cli_authorization: CliAuthorizationHttpService | None


class _CliAuthRuntime(Protocol):
    services: CliAuthApiServices


class _CliAuthAppState(Protocol):
    runtime: _CliAuthRuntime
    config: object


def get_cli_authorization_service(request: Request) -> CliAuthorizationHttpService:
    """Resolve CLI authorization without importing the global runtime bundle."""
    state = cast(_CliAuthAppState, request.app.state)
    service = state.runtime.services.cli_authorization
    if service is None:
        raise ServiceUnavailableError(resource="cli_auth")
    return service


def get_cli_verification_uri(request: Request) -> AnyHttpUrl:
    """Return the configured public browser page that accepts a user code."""
    config = getattr(request.app.state, "config", None)
    cli_auth = getattr(config, "cli_auth", None)
    verification_uri = getattr(cli_auth, "verification_uri", None)
    if not isinstance(verification_uri, AnyHttpUrl):
        raise ServiceUnavailableError(resource="cli_auth")
    return verification_uri


async def current_cli_identity(caller: NamedDependency[SkipValidation[Caller]]) -> CliIdentity:
    """Require a CLI session and retain its account, device, and session IDs."""
    if (
        caller.kind != "cli"
        or caller.account_id is None
        or caller.cli_device_id is None
        or (caller.cli_session_id is None)
    ):
        raise AuthenticationError
    return CliIdentity(
        account_id=caller.account_id,
        device_id=caller.cli_device_id,
        session_id=caller.cli_session_id,
        consent_pending=caller.consent_pending,
    )


async def enforce_anonymous_cli_idempotency(
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Keep anonymous retries in a namespace derived from the client's IP address.

    Before enrollment establishes a caller identity, different observed IP
    addresses separate clients that happen to choose the same idempotency key.
    """
    peer = request.client.host if request.client is not None else "unknown"
    peer_digest = hashlib.sha256(peer.encode()).hexdigest()
    await reserve_idempotent_request_for(request, f"cli-anonymous:{peer_digest}", idempotency_key)


CliAuthorization = NamedDependency[SkipValidation[CliAuthorizationHttpService]]
CurrentCliIdentity = NamedDependency[SkipValidation[CliIdentity]]
VerificationUri = NamedDependency[SkipValidation[AnyHttpUrl]]


@post(
    "/enrollments",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 409, 422, 429, 503),
    operation_id="cli_enrollment_start",
    security=[ANONYMOUS],
    opt=contract(
        cli=cli_command("auth.login", features=("cli-device-auth",), interaction="browser-continuation"),
    ),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_anonymous_cli_idempotency),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def start_enrollment(
    data: JSONBody[CliEnrollmentCreateRequest],
    cli: CliAuthorization,
    verification_uri: VerificationUri,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CliEnrollmentResponse:
    """Start browser approval for a client-held Ed25519 public key."""
    issued = await cli.start_enrollment(
        public_key=data.public_key_bytes(), client_instance_id=data.client_instance_id, label=data.label
    )
    return CliEnrollmentResponse.from_domain(issued, verification_uri=verification_uri)


@post(
    "/enrollments/exchange",
    responses=responses(400, 409, 422, 503),
    operation_id="cli_enrollment_exchange",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_anonymous_cli_idempotency),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def exchange_enrollment(
    data: JSONBody[CliEnrollmentExchangeRequest],
    cli: CliAuthorization,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedCliSessionResponse:
    """Exchange browser approval after proving possession of the enrolled key."""
    issued = await cli.exchange_enrollment(device_code=data.device_code, signature=data.signature_bytes())
    return IssuedCliSessionResponse.from_domain(issued)


@get(
    "/enrollments/approval",
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="cli_enrollment_preview",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def preview_enrollment(
    cli: CliAuthorization, account_id: BrowserAccountId, user_code: Annotated[UserCode, QueryParameter()]
) -> CliEnrollmentApprovalResponse:
    """Show a device label and fingerprint before the browser grants access."""
    enrollment = await cli.preview_enrollment(user_code)
    return CliEnrollmentApprovalResponse.from_domain(enrollment)


@post(
    "/enrollments/approval",
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="cli_enrollment_approve",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def approve_enrollment(
    data: JSONBody[CliEnrollmentApprovalRequest],
    cli: CliAuthorization,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CliEnrollmentApprovalResponse:
    """Approve the previewed CLI device as the signed-in browser account."""
    enrollment = await cli.approve_enrollment(user_code=data.user_code, account_id=account_id)
    return CliEnrollmentApprovalResponse.from_domain(enrollment)


@post(
    "/session-challenges",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 404, 409, 422, 429, 503),
    operation_id="cli_session_challenge_start",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_anonymous_cli_idempotency),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def start_session_challenge(
    data: JSONBody[CliSessionChallengeRequest],
    cli: CliAuthorization,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> CliSessionChallengeResponse:
    """Issue a one-time nonce for an enrolled device to sign."""
    issued = await cli.start_session_challenge(data.device_id)
    return CliSessionChallengeResponse(
        id=issued.challenge.id,
        device_id=issued.challenge.device_id,
        nonce=issued.nonce,
        expires_at=issued.challenge.expires_at.to_stdlib(),
    )


@post(
    "/sessions",
    responses=responses(400, 404, 409, 422, 503),
    operation_id="cli_session_exchange",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_anonymous_cli_idempotency),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def exchange_session_challenge(
    data: JSONBody[CliSessionExchangeRequest],
    cli: CliAuthorization,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedCliSessionResponse:
    """Exchange a signed device nonce for a short-lived bearer session."""
    issued = await cli.exchange_session_challenge(
        device_id=data.device_id, challenge_id=data.challenge_id, nonce=data.nonce, signature=data.signature_bytes()
    )
    return IssuedCliSessionResponse.from_domain(issued)


@get(
    "/devices",
    responses=responses(400, 401, 403, 503),
    operation_id="cli_devices_list",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def list_devices(cli: CliAuthorization, account_id: BrowserAccountId) -> CliDeviceListResponse:
    """List browser-account-owned devices without exposing public keys."""
    devices = await cli.list_devices(account_id)
    return CliDeviceListResponse(devices=[CliDeviceResponse.from_domain(device) for device in devices])


@delete(
    "/devices/{device_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="cli_device_revoke",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
    },
)
async def revoke_device(
    device_id: FromPath[UUID],
    cli: CliAuthorization,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> None:
    """Revoke an account-owned device and every session issued beneath it."""
    if not await cli.revoke_device(device_id=device_id, account_id=account_id):
        raise NotFoundError


@delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 422, 503),
    operation_id="cli_session_revoke",
    security=[DEVICE],
    opt=contract(cli=cli_command("auth.logout", features=("cli-device-auth",), interaction="direct")),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "cli": Provide(get_cli_authorization_service, sync_to_thread=False),
        "identity": Provide(current_cli_identity),
        "verification_uri": Provide(get_cli_verification_uri, sync_to_thread=False),
    },
)
async def revoke_current_session(
    cli: CliAuthorization, identity: CurrentCliIdentity, *, _reservation: NamedDependency[SkipValidation[None]]
) -> None:
    """Revoke the exact CLI bearer session authenticating this request."""
    if not await cli.revoke_current_session(identity):
        raise AuthenticationError


router = Router(
    path="/cli/auth",
    tags=["cli-authentication"],
    route_handlers=[
        start_enrollment,
        exchange_enrollment,
        preview_enrollment,
        approve_enrollment,
        start_session_challenge,
        exchange_session_challenge,
        list_devices,
        revoke_device,
        revoke_current_session,
    ],
    after_request=prevent_credential_storage,
)
