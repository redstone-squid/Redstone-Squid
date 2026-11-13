"""Authorize Minecraft clients to act for a player's linked account.

A Paper installation credential identifies a server, not a player. Paper and
Fabric clients both start a player challenge, ask the user to approve its code
in the browser, then exchange it for a player grant. Paper ties this exchange
to its installation's credential version; Fabric proves possession of a PKCE
verifier retained by the client. Browser routes also manage installations and
revoke grants.
"""

import hashlib
from typing import Annotated
from uuid import UUID

from litestar import Request, Router, delete, get, post, put
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import HeaderParameter, JSONBody, SkipValidation
from pydantic import AnyHttpUrl

from squid.api.contract import ANONYMOUS, PAPER, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import MinecraftInstallations, MinecraftPlayerAuthorization
from squid.api.errors import responses
from squid.api.idempotency import IdempotencyKey, reserve_idempotent_request, reserve_idempotent_request_for
from squid.api.security import BrowserAccountId, current_browser_account_id, prevent_credential_storage
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
    HeaderParameter(
        name="Squid-Installation-ID",
        schema_extra={"minLength": MIN_INSTALLATION_ID_CHARS, "maxLength": MAX_INSTALLATION_ID_CHARS},
    ),
]
InstallationSecretHeader = Annotated[
    str | None,
    HeaderParameter(
        name="Squid-Installation-Secret",
        schema_extra={"minLength": MIN_INSTALLATION_SECRET_CHARS, "maxLength": MAX_INSTALLATION_SECRET_CHARS},
    ),
]
VerificationUri = NamedDependency[SkipValidation[AnyHttpUrl]]


async def authenticated_paper_installation(
    installations: MinecraftInstallations,
    installation_id: InstallationIdHeader = None,
    installation_secret: InstallationSecretHeader = None,
) -> AuthenticatedPaperInstallation:
    """Authenticate both Paper headers without treating the installation as a player."""
    return await installations.authenticate_headers(installation_id, installation_secret)


AuthenticatedPaper = NamedDependency[SkipValidation[AuthenticatedPaperInstallation]]


async def enforce_paper_request_idempotency(
    request: Request,
    installation: AuthenticatedPaper,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Scope Paper retries to the installation and its current credential version.

    Rotating the credential starts a new namespace, so a key used with the old
    credential cannot replay its response under the replacement credential.
    """
    caller = f"minecraft-installation:{installation.id}:{installation.credential_version}"
    await reserve_idempotent_request_for(request, caller, idempotency_key)


async def enforce_fabric_request_idempotency(
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Scope anonymous Fabric retries to a digest of the client's observed IP address."""
    peer = request.client.host if request.client is not None else "unknown"
    peer_digest = hashlib.sha256(peer.encode()).hexdigest()
    await reserve_idempotent_request_for(request, f"minecraft-fabric:{peer_digest}", idempotency_key)


@post(
    "/paper/installations",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 422, 503),
    operation_id="paper_installation_create",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def create_installation(
    data: JSONBody[InstallationCreateRequest],
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedInstallationResponse:
    """Register a Paper server and return its new plaintext secret.

    Retrying with the same Idempotency-Key can replay this response. Installation
    listings omit the secret; rotate the credential if it is lost.
    """
    issued = await installations.register(
        owner_account_id=account_id, label=data.label, profile=data.profile.to_domain()
    )
    return IssuedInstallationResponse.from_domain(issued)


@get(
    "/paper/installations",
    responses=responses(400, 401, 403, 503),
    operation_id="paper_installations_list",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def list_installations(
    installations: MinecraftInstallations, account_id: BrowserAccountId
) -> InstallationListResponse:
    """List only the signed-in account's Paper installations, without secrets or digests."""
    owned = await installations.list_owned(account_id)
    return InstallationListResponse(
        installations=[InstallationResponse.from_domain(installation) for installation in owned]
    )


@post(
    "/paper/installations/{installation_id:uuid}/rotate",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="paper_installation_rotate",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def rotate_installation(
    installation_id: UUID,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedInstallationResponse:
    """Revoke an owned installation's old credential and return its replacement secret."""
    issued = await installations.rotate(installation_id=installation_id, owner_account_id=account_id)
    return IssuedInstallationResponse.from_domain(issued)


@put(
    "/paper/installations/{installation_id:uuid}/profile",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="paper_installation_profile_update",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def update_installation_profile(
    installation_id: UUID,
    data: JSONBody[ServerProfileSchema],
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> InstallationResponse:
    """Replace an owned server's explicit public-listing and sponsorship preferences."""
    installation = await installations.update_profile(
        installation_id=installation_id, owner_account_id=account_id, profile=data.to_domain()
    )
    return InstallationResponse.from_domain(installation)


@delete(
    "/paper/installations/{installation_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="paper_installation_revoke",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def revoke_installation(
    installation_id: UUID,
    installations: MinecraftInstallations,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> None:
    """Revoke an owned server and all pending or active authorization derived from it."""
    await installations.revoke(installation_id=installation_id, owner_account_id=account_id)


@post(
    "/paper/challenges",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 409, 422, 429, 503),
    operation_id="paper_challenge_start",
    opt=contract(security=[PAPER], cli=transport_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_paper_request_idempotency),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def start_paper_challenge(
    data: JSONBody[PaperChallengeCreateRequest],
    players: MinecraftPlayerAuthorization,
    installation: AuthenticatedPaper,
    verification_uri: VerificationUri,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> ChallengeCreateResponse:
    """Start player authorization bound to the authenticated Paper credential version."""
    challenge = await players.start_paper_challenge(installation=installation, java_uuid=data.java_uuid)
    return ChallengeCreateResponse.from_domain(challenge, verification_uri=verification_uri)


@post(
    "/paper/challenges/exchange",
    responses=responses(400, 401, 409, 422, 503),
    operation_id="paper_challenge_exchange",
    opt=contract(security=[PAPER], cli=transport_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_paper_request_idempotency),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def exchange_paper_challenge(
    data: JSONBody[PaperChallengeExchangeRequest],
    players: MinecraftPlayerAuthorization,
    installation: AuthenticatedPaper,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Paper challenge on the same authenticated installation."""
    issued = await players.exchange_paper(device_code=data.device_code, installation=installation)
    return IssuedPlayerGrantResponse.from_domain(issued)


@post(
    "/fabric/challenges",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 409, 422, 429, 503),
    operation_id="fabric_challenge_start",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_fabric_request_idempotency),
        "verification_uri": Provide(get_minecraft_verification_uri),
        "installation": Provide(authenticated_paper_installation),
    },
)
async def start_fabric_challenge(
    data: JSONBody[FabricChallengeCreateRequest],
    players: MinecraftPlayerAuthorization,
    verification_uri: VerificationUri,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> ChallengeCreateResponse:
    """Start Fabric authorization using the client's S256 PKCE challenge.

    The client keeps its verifier and supplies it when exchanging the approved
    challenge for a player grant.
    """
    challenge = await players.start_fabric_challenge(
        java_uuid=data.java_uuid, pkce_s256_challenge=data.pkce_s256_challenge
    )
    return ChallengeCreateResponse.from_domain(challenge, verification_uri=verification_uri)


@post(
    "/fabric/challenges/exchange",
    responses=responses(400, 409, 422, 503),
    operation_id="fabric_challenge_exchange",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(enforce_fabric_request_idempotency),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def exchange_fabric_challenge(
    data: JSONBody[FabricChallengeExchangeRequest],
    players: MinecraftPlayerAuthorization,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> IssuedPlayerGrantResponse:
    """Exchange one approved Fabric challenge by proving its client-held PKCE verifier."""
    issued = await players.exchange_fabric(device_code=data.device_code, pkce_verifier=data.pkce_verifier)
    return IssuedPlayerGrantResponse.from_domain(issued)


@post(
    "/challenges/approval",
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="minecraft_challenge_approve",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def approve_challenge(
    data: JSONBody[ChallengeApprovalRequest],
    players: MinecraftPlayerAuthorization,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> ChallengeApprovalResponse:
    """Approve only the Java UUID authoritatively attached to the signed-in account."""
    challenge = await players.approve(user_code=data.user_code, account_id=account_id)
    return ChallengeApprovalResponse.from_domain(challenge)


@delete(
    "/grants/{grant_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="minecraft_grant_revoke",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "account_id": Provide(current_browser_account_id),
        "_reservation": Provide(reserve_idempotent_request),
        "installation": Provide(authenticated_paper_installation),
        "verification_uri": Provide(get_minecraft_verification_uri),
    },
)
async def revoke_grant(
    grant_id: UUID,
    players: MinecraftPlayerAuthorization,
    account_id: BrowserAccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> None:
    """Revoke one grant only when it belongs to the signed-in account."""
    revoked = await players.revoke_grant(grant_id=grant_id, account_id=account_id)
    if not revoked:
        raise NotFoundError


router = Router(
    path="/minecraft/auth",
    tags=["minecraft-authentication"],
    route_handlers=[
        create_installation,
        list_installations,
        rotate_installation,
        update_installation_profile,
        revoke_installation,
        start_paper_challenge,
        exchange_paper_challenge,
        start_fabric_challenge,
        exchange_fabric_challenge,
        approve_challenge,
        revoke_grant,
    ],
    after_request=prevent_credential_storage,
)
