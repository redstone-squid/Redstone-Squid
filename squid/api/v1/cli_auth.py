"""Strict HTTP routes for browser-approved CLI device authorization."""

import hashlib
from collections.abc import Awaitable
from typing import Annotated, Protocol, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import AnyHttpUrl

from squid.accounts.errors import ConsentRequiredError
from squid.api.errors import responses
from squid.api.idempotency import IdempotencyKey, enforce_request_idempotency, enforce_request_idempotency_for
from squid.api.security import Caller, current_caller
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
from squid.cli_auth.domain import (
    CliDevice,
    CliDeviceEnrollment,
    CliIdentity,
    IssuedCliEnrollment,
    IssuedCliSession,
    IssuedCliSessionChallenge,
)
from squid.core.errors import AuthenticationError, NotFoundError, ServiceUnavailableError

_T = TypeVar("_T")


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


async def current_browser_account_id(caller: Annotated[Caller, Depends(current_caller)]) -> int:
    """Require a signed-in browser account with current privacy consent."""
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    if caller.consent_pending:
        raise ConsentRequiredError(account_id=caller.account_id)
    return caller.account_id


async def current_cli_identity(caller: Annotated[Caller, Depends(current_caller)]) -> CliIdentity:
    """Require an authenticated CLI session with its exact device provenance."""
    if (
        caller.kind != "cli"
        or caller.account_id is None
        or caller.cli_device_id is None
        or caller.cli_session_id is None
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
    """Partition one-time anonymous responses by a digest of the observed peer."""
    peer = request.client.host if request.client is not None else "unknown"
    peer_digest = hashlib.sha256(peer.encode()).hexdigest()
    await enforce_request_idempotency_for(request, f"cli-anonymous:{peer_digest}", idempotency_key)


CliAuthorization = Annotated[CliAuthorizationHttpService, Depends(get_cli_authorization_service)]
BrowserAccountId = Annotated[int, Depends(current_browser_account_id)]
CurrentCliIdentity = Annotated[CliIdentity, Depends(current_cli_identity)]
VerificationUri = Annotated[AnyHttpUrl, Depends(get_cli_verification_uri)]

router = APIRouter(prefix="/cli/auth", tags=["cli-authentication"])


@router.post(
    "/enrollments",
    response_model=CliEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 409, 422, 429, 503),
    dependencies=[Depends(enforce_anonymous_cli_idempotency)],
)
async def start_enrollment(
    payload: CliEnrollmentCreateRequest,
    response: Response,
    cli: CliAuthorization,
    verification_uri: VerificationUri,
) -> CliEnrollmentResponse:
    """Start browser approval for a client-held Ed25519 public key."""
    issued = await _execute(
        cli.start_enrollment(
            public_key=payload.public_key_bytes(),
            client_instance_id=payload.client_instance_id,
            label=payload.label,
        )
    )
    _prevent_storage(response)
    return CliEnrollmentResponse.from_domain(issued, verification_uri=verification_uri)


@router.post(
    "/enrollments/exchange",
    response_model=IssuedCliSessionResponse,
    responses=responses(400, 409, 422, 503),
    dependencies=[Depends(enforce_anonymous_cli_idempotency)],
)
async def exchange_enrollment(
    payload: CliEnrollmentExchangeRequest,
    response: Response,
    cli: CliAuthorization,
) -> IssuedCliSessionResponse:
    """Exchange browser approval after proving possession of the enrolled key."""
    issued = await _execute(
        cli.exchange_enrollment(device_code=payload.device_code, signature=payload.signature_bytes())
    )
    _prevent_storage(response)
    return IssuedCliSessionResponse.from_domain(issued)


@router.get(
    "/enrollments/approval",
    response_model=CliEnrollmentApprovalResponse,
    responses=responses(400, 401, 403, 409, 422, 503),
)
async def preview_enrollment(
    response: Response,
    cli: CliAuthorization,
    _account_id: BrowserAccountId,
    user_code: Annotated[UserCode, Query()],
) -> CliEnrollmentApprovalResponse:
    """Show a device label and fingerprint before the browser grants access."""
    enrollment = await _execute(cli.preview_enrollment(user_code))
    _prevent_storage(response)
    return CliEnrollmentApprovalResponse.from_domain(enrollment)


@router.post(
    "/enrollments/approval",
    response_model=CliEnrollmentApprovalResponse,
    responses=responses(400, 401, 403, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def approve_enrollment(
    payload: CliEnrollmentApprovalRequest,
    response: Response,
    cli: CliAuthorization,
    account_id: BrowserAccountId,
) -> CliEnrollmentApprovalResponse:
    """Approve the previewed CLI device as the signed-in browser account."""
    enrollment = await _execute(cli.approve_enrollment(user_code=payload.user_code, account_id=account_id))
    _prevent_storage(response)
    return CliEnrollmentApprovalResponse.from_domain(enrollment)


@router.post(
    "/session-challenges",
    response_model=CliSessionChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 404, 409, 422, 429, 503),
    dependencies=[Depends(enforce_anonymous_cli_idempotency)],
)
async def start_session_challenge(
    payload: CliSessionChallengeRequest,
    response: Response,
    cli: CliAuthorization,
) -> CliSessionChallengeResponse:
    """Issue a one-time nonce for an enrolled device to sign."""
    issued = await _execute(cli.start_session_challenge(payload.device_id))
    _prevent_storage(response)
    return CliSessionChallengeResponse(
        id=issued.challenge.id,
        device_id=issued.challenge.device_id,
        nonce=issued.nonce,
        expires_at=issued.challenge.expires_at.to_stdlib(),
    )


@router.post(
    "/sessions",
    response_model=IssuedCliSessionResponse,
    responses=responses(400, 404, 409, 422, 503),
    dependencies=[Depends(enforce_anonymous_cli_idempotency)],
)
async def exchange_session_challenge(
    payload: CliSessionExchangeRequest,
    response: Response,
    cli: CliAuthorization,
) -> IssuedCliSessionResponse:
    """Exchange a signed device nonce for a short-lived bearer session."""
    issued = await _execute(
        cli.exchange_session_challenge(
            device_id=payload.device_id,
            challenge_id=payload.challenge_id,
            nonce=payload.nonce,
            signature=payload.signature_bytes(),
        )
    )
    _prevent_storage(response)
    return IssuedCliSessionResponse.from_domain(issued)


@router.get(
    "/devices",
    response_model=CliDeviceListResponse,
    responses=responses(400, 401, 403, 503),
)
async def list_devices(
    response: Response,
    cli: CliAuthorization,
    account_id: BrowserAccountId,
) -> CliDeviceListResponse:
    """List browser-account-owned devices without exposing public keys."""
    devices = await _execute(cli.list_devices(account_id))
    _prevent_storage(response)
    return CliDeviceListResponse(devices=[CliDeviceResponse.from_domain(device) for device in devices])


@router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def revoke_device(
    device_id: UUID,
    cli: CliAuthorization,
    account_id: BrowserAccountId,
) -> Response:
    """Revoke an account-owned device and every session issued beneath it."""
    if not await _execute(cli.revoke_device(device_id=device_id, account_id=account_id)):
        raise NotFoundError
    return _empty_no_store()


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def revoke_current_session(
    cli: CliAuthorization,
    identity: CurrentCliIdentity,
) -> Response:
    """Revoke the exact CLI bearer session authenticating this request."""
    if not await _execute(cli.revoke_current_session(identity)):
        raise AuthenticationError
    return _empty_no_store()


async def _execute(operation: Awaitable[_T]) -> _T:
    return await operation


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _empty_no_store() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _prevent_storage(response)
    return response
