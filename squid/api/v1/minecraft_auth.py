"""Strict HTTP routes for Minecraft installation and player authorization."""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import AnyHttpUrl

from squid.api.contract import ANONYMOUS, PAPER, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import MinecraftInstallations, MinecraftPlayerAuthorization
from squid.api.errors import responses
from squid.api.idempotency import IdempotencyKey, reserve_idempotent_request, reserve_idempotent_request_for
from squid.api.security import BrowserAccountId, prevent_credential_storage
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
from squid.core.errors import NotFoundError, ServiceUnavailableError
from squid.minecraft_auth.application.crypto import (
    MAX_INSTALLATION_ID_CHARS,
    MAX_INSTALLATION_SECRET_CHARS,
    MIN_INSTALLATION_ID_CHARS,
    MIN_INSTALLATION_SECRET_CHARS,
)
from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
)


async def get_minecraft_verification_uri(request: Request) -> AnyHttpUrl:
    """Return the explicitly configured public page that accepts a user code."""
    config = getattr(request.app.state, "config", None)
    minecraft_auth = getattr(config, "minecraft_auth", None)
    verification_uri = getattr(minecraft_auth, "verification_uri", None)
    if not isinstance(verification_uri, AnyHttpUrl):
        raise ServiceUnavailableError(resource="minecraft_auth")
    return verification_uri


InstallationIdHeader = Annotated[
    str | None,
    Header(
        alias="Squid-Installation-ID",
        json_schema_extra={"minLength": MIN_INSTALLATION_ID_CHARS, "maxLength": MAX_INSTALLATION_ID_CHARS},
    ),
]
InstallationSecretHeader = Annotated[
    str | None,
    Header(
        alias="Squid-Installation-Secret",
        json_schema_extra={
            "minLength": MIN_INSTALLATION_SECRET_CHARS,
            "maxLength": MAX_INSTALLATION_SECRET_CHARS,
        },
    ),
]
VerificationUri = Annotated[AnyHttpUrl, Depends(get_minecraft_verification_uri)]


async def authenticated_paper_installation(
    installations: MinecraftInstallations,
    installation_id: InstallationIdHeader = None,
    installation_secret: InstallationSecretHeader = None,
) -> AuthenticatedPaperInstallation:
    """Authenticate both Paper headers without treating the installation as a player."""
    return await installations.authenticate_headers(installation_id, installation_secret)


AuthenticatedPaper = Annotated[AuthenticatedPaperInstallation, Depends(authenticated_paper_installation)]


async def enforce_paper_request_idempotency(
    request: Request,
    installation: AuthenticatedPaper,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Partition one-time Paper responses by an authenticated credential generation."""
    caller = f"minecraft-installation:{installation.id}:{installation.credential_version}"
    await reserve_idempotent_request_for(request, caller, idempotency_key)


async def enforce_fabric_request_idempotency(
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Partition anonymous Fabric retries by the transport's observed network peer."""
    peer = request.client.host if request.client is not None else "unknown"
    peer_digest = hashlib.sha256(peer.encode()).hexdigest()
    await reserve_idempotent_request_for(request, f"minecraft-fabric:{peer_digest}", idempotency_key)


router = APIRouter(
    prefix="/minecraft/auth",
    tags=["minecraft-authentication"],
    dependencies=[Depends(prevent_credential_storage)],
)


@router.post(
    "/paper/installations",
    response_model=IssuedInstallationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="paper_installation_create",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def create_installation(
    payload: InstallationCreateRequest,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
) -> IssuedInstallationResponse:
    """Register a Paper server and return its plaintext secret exactly once."""
    issued = await installations.register(
        owner_account_id=account_id,
        label=payload.label,
        profile=payload.profile.to_domain(),
    )
    return IssuedInstallationResponse.from_domain(issued)


@router.get(
    "/paper/installations",
    response_model=InstallationListResponse,
    responses=responses(400, 401, 403, 503),
    operation_id="paper_installations_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_installations(
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
) -> InstallationListResponse:
    """List only the signed-in account's Paper installations, without secrets or digests."""
    owned = await installations.list_owned(account_id)
    return InstallationListResponse(
        installations=[InstallationResponse.from_domain(installation) for installation in owned]
    )


@router.post(
    "/paper/installations/{installation_id}/rotate",
    response_model=IssuedInstallationResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="paper_installation_rotate",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def rotate_installation(
    installation_id: UUID,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
) -> IssuedInstallationResponse:
    """Fence an owned installation's old credentials and return one replacement secret."""
    issued = await installations.rotate(installation_id=installation_id, owner_account_id=account_id)
    return IssuedInstallationResponse.from_domain(issued)


@router.put(
    "/paper/installations/{installation_id}/profile",
    response_model=InstallationResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="paper_installation_profile_update",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def update_installation_profile(
    installation_id: UUID,
    payload: ServerProfileSchema,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
) -> InstallationResponse:
    """Replace an owned server's explicit public-listing and sponsorship preferences."""
    installation = await installations.update_profile(
        installation_id=installation_id,
        owner_account_id=account_id,
        profile=payload.to_domain(),
    )
    return InstallationResponse.from_domain(installation)


@router.delete(
    "/paper/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="paper_installation_revoke",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def revoke_installation(
    installation_id: UUID,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
) -> None:
    """Revoke an owned server and all pending or active authorization derived from it."""
    await installations.revoke(installation_id=installation_id, owner_account_id=account_id)


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
    players: MinecraftPlayerAuthorization,
    installation: AuthenticatedPaper,
    verification_uri: VerificationUri,
) -> ChallengeCreateResponse:
    """Start player authorization bound to the authenticated Paper credential generation."""
    challenge = await players.start_paper_challenge(installation=installation, java_uuid=payload.java_uuid)
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
    players: MinecraftPlayerAuthorization,
    installation: AuthenticatedPaper,
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Paper challenge on the same authenticated installation."""
    issued = await players.exchange_paper(device_code=payload.device_code, installation=installation)
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
    players: MinecraftPlayerAuthorization,
    verification_uri: VerificationUri,
) -> ChallengeCreateResponse:
    """Start an anonymous Fabric challenge committed to a client-held S256 verifier."""
    challenge = await players.start_fabric_challenge(
        java_uuid=payload.java_uuid,
        pkce_s256_challenge=payload.pkce_s256_challenge,
    )
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
    players: MinecraftPlayerAuthorization,
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Fabric challenge by proving its client-held PKCE verifier."""
    issued = await players.exchange_fabric(device_code=payload.device_code, pkce_verifier=payload.pkce_verifier)
    return IssuedPlayerGrantResponse.from_domain(issued)


@router.post(
    "/challenges/approval",
    response_model=ChallengeApprovalResponse,
    responses=responses(400, 401, 403, 409, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="minecraft_challenge_approve",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def approve_challenge(
    payload: ChallengeApprovalRequest,
    players: MinecraftPlayerAuthorization,
    account_id: BrowserAccountId,
) -> ChallengeApprovalResponse:
    """Approve only the Java UUID authoritatively attached to the signed-in account."""
    challenge = await players.approve(user_code=payload.user_code, account_id=account_id)
    return ChallengeApprovalResponse.from_domain(challenge)


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    operation_id="minecraft_grant_revoke",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def revoke_grant(
    grant_id: UUID,
    players: MinecraftPlayerAuthorization,
    account_id: BrowserAccountId,
) -> None:
    """Revoke one grant only when it belongs to the signed-in account."""
    revoked = await players.revoke_grant(grant_id=grant_id, account_id=account_id)
    if not revoked:
        raise NotFoundError
