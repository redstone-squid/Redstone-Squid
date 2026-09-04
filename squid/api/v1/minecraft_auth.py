"""Strict HTTP routes for Minecraft installation and player authorization."""

import hashlib
from collections.abc import Awaitable
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import AnyHttpUrl

from squid.api.contract import ANONYMOUS, PAPER, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.errors import responses
from squid.api.idempotency import IdempotencyKey, enforce_request_idempotency, enforce_request_idempotency_for
from squid.api.security import Caller, current_caller, require_consented_account
from squid.api.v1.schemas.minecraft_auth import (
    ChallengeApprovalRequest,
    ChallengeApprovalResponse,
    ChallengeCreateResponse,
    FabricChallengeCreateRequest,
    FabricChallengeExchangeRequest,
    InstallationCreateRequest,
    InstallationListResponse,
    InstallationResponse,
    IssuedInstallationResponse,
    IssuedPlayerGrantResponse,
    PaperChallengeCreateRequest,
    PaperChallengeExchangeRequest,
    ServerProfileSchema,
)
from squid.core.errors import AuthenticationError, NotFoundError, ServiceUnavailableError
from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PublicServerProfile,
)

_NO_STORE = "no-store"


class PaperInstallationHttpService(Protocol):
    """Account and credential operations consumed by the HTTP transport."""

    async def register(
        self,
        *,
        owner_account_id: int,
        label: str,
        profile: PublicServerProfile | None = None,
    ) -> IssuedInstallationCredential: ...

    async def list_owned(self, owner_account_id: int) -> tuple[PaperInstallation, ...]: ...

    async def rotate(self, *, installation_id: UUID, owner_account_id: int) -> IssuedInstallationCredential: ...

    async def revoke(self, *, installation_id: UUID, owner_account_id: int) -> PaperInstallation: ...

    async def update_profile(
        self,
        *,
        installation_id: UUID,
        owner_account_id: int,
        profile: PublicServerProfile,
    ) -> PaperInstallation: ...

    async def authenticate(self, token: str) -> AuthenticatedPaperInstallation: ...

    async def authenticate_headers(
        self,
        installation_id: str | None,
        installation_secret: str | None,
    ) -> AuthenticatedPaperInstallation: ...


class PlayerAuthorizationHttpService(Protocol):
    """Challenge and grant operations consumed by the HTTP transport."""

    async def start_paper_challenge(
        self,
        *,
        installation: AuthenticatedPaperInstallation,
        java_uuid: UUID,
    ) -> IssuedPlayerChallenge: ...

    async def start_fabric_challenge(
        self,
        *,
        java_uuid: UUID,
        pkce_s256_challenge: str,
    ) -> IssuedPlayerChallenge: ...

    async def approve(self, *, user_code: str, account_id: int) -> PlayerAuthorizationChallenge: ...

    async def exchange_paper(
        self,
        *,
        device_code: str,
        installation: AuthenticatedPaperInstallation,
    ) -> IssuedPlayerGrant: ...

    async def exchange_fabric(self, *, device_code: str, pkce_verifier: str) -> IssuedPlayerGrant: ...

    async def revoke_grant(self, *, grant_id: UUID, account_id: int) -> bool: ...


class MinecraftAuthApiServices(Protocol):
    """Narrow runtime bundle required when this router is integrated."""

    minecraft_installations: PaperInstallationHttpService | None
    minecraft_player_authorization: PlayerAuthorizationHttpService | None


class _MinecraftAuthRuntime(Protocol):
    services: MinecraftAuthApiServices


class _MinecraftAuthAppState(Protocol):
    runtime: _MinecraftAuthRuntime
    config: object


def get_installation_service(request: Request) -> PaperInstallationHttpService:
    """Resolve Paper installation operations without importing the global runtime."""
    state = cast(_MinecraftAuthAppState, request.app.state)
    service = state.runtime.services.minecraft_installations
    if service is None:
        raise ServiceUnavailableError(resource="minecraft_auth")
    return service


def get_player_authorization_service(request: Request) -> PlayerAuthorizationHttpService:
    """Resolve player authorization operations without importing the global runtime."""
    state = cast(_MinecraftAuthAppState, request.app.state)
    service = state.runtime.services.minecraft_player_authorization
    if service is None:
        raise ServiceUnavailableError(resource="minecraft_auth")
    return service


def get_minecraft_verification_uri(request: Request) -> AnyHttpUrl:
    """Return the explicitly configured public page that accepts a user code."""
    config = getattr(request.app.state, "config", None)
    minecraft_auth = getattr(config, "minecraft_auth", None)
    verification_uri = getattr(minecraft_auth, "verification_uri", None)
    if not isinstance(verification_uri, AnyHttpUrl):
        raise ServiceUnavailableError(resource="minecraft_auth")
    return verification_uri


async def current_account_id(caller: Annotated[Caller, Depends(current_caller)]) -> int:
    """Require a signed-in human account with current privacy consent."""
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    return require_consented_account(caller)


Installations = Annotated[PaperInstallationHttpService, Depends(get_installation_service)]
PlayerAuthorization = Annotated[PlayerAuthorizationHttpService, Depends(get_player_authorization_service)]
AccountId = Annotated[int, Depends(current_account_id)]
InstallationIdHeader = Annotated[str | None, Header(alias="Squid-Installation-ID")]
InstallationSecretHeader = Annotated[str | None, Header(alias="Squid-Installation-Secret")]
VerificationUri = Annotated[AnyHttpUrl, Depends(get_minecraft_verification_uri)]


async def authenticated_paper_installation(
    installations: Installations,
    installation_id: InstallationIdHeader = None,
    installation_secret: InstallationSecretHeader = None,
) -> AuthenticatedPaperInstallation:
    """Authenticate both Paper headers without treating the installation as a player."""
    return await _execute(installations.authenticate_headers(installation_id, installation_secret))


AuthenticatedPaper = Annotated[AuthenticatedPaperInstallation, Depends(authenticated_paper_installation)]


async def enforce_paper_request_idempotency(
    request: Request,
    installation: AuthenticatedPaper,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Partition one-time Paper responses by an authenticated credential generation."""
    caller = f"minecraft-installation:{installation.id}:{installation.credential_version}"
    await enforce_request_idempotency_for(request, caller, idempotency_key)


async def enforce_fabric_request_idempotency(
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Partition anonymous Fabric retries by the transport's observed network peer."""
    peer = request.client.host if request.client is not None else "unknown"
    peer_digest = hashlib.sha256(peer.encode()).hexdigest()
    await enforce_request_idempotency_for(request, f"minecraft-fabric:{peer_digest}", idempotency_key)


router = APIRouter(
    prefix="/minecraft/auth",
    tags=["minecraft-authentication"],
)


@router.post(
    "/paper/installations",
    response_model=IssuedInstallationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="paper_installation_create",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def create_installation(
    payload: InstallationCreateRequest,
    response: Response,
    installations: Installations,
    account_id: AccountId,
) -> IssuedInstallationResponse:
    """Register a Paper server and return its plaintext secret exactly once."""
    issued = await _execute(
        installations.register(
            owner_account_id=account_id,
            label=payload.label,
            profile=payload.profile.to_domain(),
        )
    )
    _prevent_storage(response)
    return IssuedInstallationResponse.from_domain(issued)


@router.get(
    "/paper/installations",
    response_model=InstallationListResponse,
    responses=responses(400, 401, 403, 503),
    operation_id="paper_installations_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_installations(
    response: Response,
    installations: Installations,
    account_id: AccountId,
) -> InstallationListResponse:
    """List only the signed-in account's Paper installations, without secrets or digests."""
    owned = await _execute(installations.list_owned(account_id))
    _prevent_storage(response)
    return InstallationListResponse(
        installations=[InstallationResponse.from_domain(installation) for installation in owned]
    )


@router.post(
    "/paper/installations/{installation_id}/rotate",
    response_model=IssuedInstallationResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="paper_installation_rotate",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def rotate_installation(
    installation_id: UUID,
    response: Response,
    installations: Installations,
    account_id: AccountId,
) -> IssuedInstallationResponse:
    """Fence an owned installation's old credentials and return one replacement secret."""
    issued = await _execute(installations.rotate(installation_id=installation_id, owner_account_id=account_id))
    _prevent_storage(response)
    return IssuedInstallationResponse.from_domain(issued)


@router.put(
    "/paper/installations/{installation_id}/profile",
    response_model=InstallationResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="paper_installation_profile_update",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def update_installation_profile(
    installation_id: UUID,
    payload: ServerProfileSchema,
    response: Response,
    installations: Installations,
    account_id: AccountId,
) -> InstallationResponse:
    """Replace an owned server's explicit public-listing and sponsorship preferences."""
    installation = await _execute(
        installations.update_profile(
            installation_id=installation_id,
            owner_account_id=account_id,
            profile=payload.to_domain(),
        )
    )
    _prevent_storage(response)
    return InstallationResponse.from_domain(installation)


@router.delete(
    "/paper/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="paper_installation_revoke",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def revoke_installation(
    installation_id: UUID,
    installations: Installations,
    account_id: AccountId,
) -> Response:
    """Revoke an owned server and all pending or active authorization derived from it."""
    await _execute(installations.revoke(installation_id=installation_id, owner_account_id=account_id))
    return _empty_no_store()


@router.post(
    "/paper/challenges",
    response_model=ChallengeCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 409, 422, 429, 503),
    dependencies=[Depends(enforce_paper_request_idempotency)],
    operation_id="paper_challenge_start",
    openapi_extra=contract(security=[PAPER], cli=transport_only()),
)
async def start_paper_challenge(
    payload: PaperChallengeCreateRequest,
    response: Response,
    players: PlayerAuthorization,
    installation: AuthenticatedPaper,
    verification_uri: VerificationUri,
) -> ChallengeCreateResponse:
    """Start player authorization bound to the authenticated Paper credential generation."""
    challenge = await _execute(players.start_paper_challenge(installation=installation, java_uuid=payload.java_uuid))
    _prevent_storage(response)
    return ChallengeCreateResponse.from_domain(challenge, verification_uri=verification_uri)


@router.post(
    "/paper/challenges/exchange",
    response_model=IssuedPlayerGrantResponse,
    responses=responses(400, 401, 409, 422, 503),
    dependencies=[Depends(enforce_paper_request_idempotency)],
    operation_id="paper_challenge_exchange",
    openapi_extra=contract(security=[PAPER], cli=transport_only()),
)
async def exchange_paper_challenge(
    payload: PaperChallengeExchangeRequest,
    response: Response,
    players: PlayerAuthorization,
    installation: AuthenticatedPaper,
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Paper challenge on the same authenticated installation."""
    issued = await _execute(players.exchange_paper(device_code=payload.device_code, installation=installation))
    _prevent_storage(response)
    return IssuedPlayerGrantResponse.from_domain(issued)


@router.post(
    "/fabric/challenges",
    response_model=ChallengeCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 409, 422, 429, 503),
    dependencies=[Depends(enforce_fabric_request_idempotency)],
    operation_id="fabric_challenge_start",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def start_fabric_challenge(
    payload: FabricChallengeCreateRequest,
    response: Response,
    players: PlayerAuthorization,
    verification_uri: VerificationUri,
) -> ChallengeCreateResponse:
    """Start an anonymous Fabric challenge committed to a client-held S256 verifier."""
    challenge = await _execute(
        players.start_fabric_challenge(
            java_uuid=payload.java_uuid,
            pkce_s256_challenge=payload.pkce_s256_challenge,
        )
    )
    _prevent_storage(response)
    return ChallengeCreateResponse.from_domain(challenge, verification_uri=verification_uri)


@router.post(
    "/fabric/challenges/exchange",
    response_model=IssuedPlayerGrantResponse,
    responses=responses(400, 409, 422, 503),
    dependencies=[Depends(enforce_fabric_request_idempotency)],
    operation_id="fabric_challenge_exchange",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def exchange_fabric_challenge(
    payload: FabricChallengeExchangeRequest,
    response: Response,
    players: PlayerAuthorization,
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Fabric challenge by proving its client-held PKCE verifier."""
    issued = await _execute(
        players.exchange_fabric(device_code=payload.device_code, pkce_verifier=payload.pkce_verifier)
    )
    _prevent_storage(response)
    return IssuedPlayerGrantResponse.from_domain(issued)


@router.post(
    "/challenges/approval",
    response_model=ChallengeApprovalResponse,
    responses=responses(400, 401, 403, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="minecraft_challenge_approve",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def approve_challenge(
    payload: ChallengeApprovalRequest,
    response: Response,
    players: PlayerAuthorization,
    account_id: AccountId,
) -> ChallengeApprovalResponse:
    """Approve only the Java UUID authoritatively attached to the signed-in account."""
    challenge = await _execute(players.approve(user_code=payload.user_code, account_id=account_id))
    _prevent_storage(response)
    return ChallengeApprovalResponse.from_domain(challenge)


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="minecraft_grant_revoke",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def revoke_grant(
    grant_id: UUID,
    players: PlayerAuthorization,
    account_id: AccountId,
) -> Response:
    """Revoke one grant only when it belongs to the signed-in account."""
    revoked = await _execute(players.revoke_grant(grant_id=grant_id, account_id=account_id))
    if not revoked:
        raise NotFoundError
    return _empty_no_store()


async def _execute[T](operation: Awaitable[T]) -> T:
    return await operation


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = _NO_STORE
    response.headers["Pragma"] = "no-cache"


def _empty_no_store() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _prevent_storage(response)
    return response
