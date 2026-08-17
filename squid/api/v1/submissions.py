"""Submission form and synchronized-draft routes.

The form describes fields and constraints; whether they become a Discord modal,
an HTML form, or a CLI prompt is the client's decision.
"""

from dataclasses import dataclass
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from squid.accounts.errors import ConsentRequiredError
from squid.api.contract import ANONYMOUS, DEVICE, MINECRAFT, WEB, WEB_WRITE, cli_command, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.idempotency import enforce_request_idempotency
from squid.api.security import Caller, current_caller
from squid.api.v1.schemas.submissions import (
    DraftChangeRequest,
    DraftChangeResponse,
    DraftCreateRequest,
    DraftListResponse,
    FormManifestResponse,
    FormOptionSetResponse,
    StoredDraftResponse,
    SubmissionFinalizationResponse,
)
from squid.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from squid.submissions.application import AppliedDraftChange, FinalizationJobSnapshot, FormOptionSet, StoredDraft
from squid.submissions.domain import DraftChange, FormManifest, SubmissionOrigin


class SubmissionFormReader(Protocol):
    """Form reads needed by the HTTP transport."""

    def manifest(self, *, locale: str | None) -> FormManifest: ...

    async def manifest_revision(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None: ...

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet: ...


class SubmissionDraftCommands(Protocol):
    """Account-owned draft operations needed by the HTTP transport."""

    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        source_installation_id: UUID | None = None,
    ) -> StoredDraft: ...

    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]: ...

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft: ...

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        locale: str | None,
    ) -> AppliedDraftChange: ...

    async def delete(self, draft_id: UUID, account_id: int) -> None: ...


class SubmissionFinalizationCommands(Protocol):
    """Owner-scoped finalization operations needed by the HTTP transport."""

    async def submit(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        locale: str | None,
    ) -> FinalizationJobSnapshot: ...

    async def status(self, draft_id: UUID, account_id: int) -> FinalizationJobSnapshot | None: ...


class SubmissionApiServices(Protocol):
    """Narrow runtime service bundle consumed by this router."""

    submission_forms: SubmissionFormReader
    submission_drafts: SubmissionDraftCommands
    submission_finalization: SubmissionFinalizationCommands


class _SubmissionRuntime(Protocol):
    services: SubmissionApiServices


class _SubmissionAppState(Protocol):
    runtime: _SubmissionRuntime


def get_submission_forms(request: Request) -> SubmissionFormReader:
    """Resolve form reads without coupling this module to the global runtime type."""
    state = cast(_SubmissionAppState, request.app.state)
    return state.runtime.services.submission_forms


def get_submission_drafts(request: Request) -> SubmissionDraftCommands:
    """Resolve draft commands without coupling this module to the global runtime type."""
    state = cast(_SubmissionAppState, request.app.state)
    return state.runtime.services.submission_drafts


def get_submission_finalization(request: Request) -> SubmissionFinalizationCommands:
    """Resolve finalization without coupling this module to the global runtime type."""
    state = cast(_SubmissionAppState, request.app.state)
    return state.runtime.services.submission_finalization


class SubmissionFinalizationNotFoundError(NotFoundError):
    """The owned draft has not been submitted for finalization yet."""

    default_message = "This draft has not been submitted yet."
    default_title = "Submission not started"
    default_resource = "submission_finalization"


class SubmissionFormRevisionNotFoundError(NotFoundError):
    """The requested immutable form revision is not available from this deployment."""

    default_message = "This submission form revision is not available."
    default_title = "Submission form revision not found"
    default_resource = "submission_form_revision"


async def authenticated_account(
    caller: Annotated[Caller, Depends(current_caller)],
) -> int:
    """Require a current human or player-bound account for draft access."""
    return _submission_actor(caller).account_id


@dataclass(frozen=True, slots=True)
class AuthenticatedSubmissionActor:
    """Server-derived account and renderer origin for one submission request."""

    account_id: int
    origin: SubmissionOrigin
    java_uuid: UUID | None = None
    installation_id: UUID | None = None
    grant_id: UUID | None = None


async def authenticated_submission_actor(
    caller: Annotated[Caller, Depends(current_caller)],
) -> AuthenticatedSubmissionActor:
    """Resolve submission provenance without trusting a request-body origin."""
    return _submission_actor(caller)


def _submission_actor(caller: Caller) -> AuthenticatedSubmissionActor:
    if caller.account_id is None or caller.kind not in {"account", "cli", "minecraft_player"}:
        raise AuthenticationError
    if caller.consent_pending:
        raise ConsentRequiredError(account_id=caller.account_id)
    if caller.kind == "account":
        origin = SubmissionOrigin.WEB
    elif caller.kind == "cli":
        if caller.cli_device_id is None or caller.cli_session_id is None:
            raise AuthenticationError
        origin = SubmissionOrigin.CLI
    elif caller.minecraft_origin is not None:
        origin = SubmissionOrigin(caller.minecraft_origin)
    else:
        raise AuthenticationError
    if caller.kind == "minecraft_player":
        if caller.java_uuid is None or caller.grant_id is None:
            raise AuthenticationError
        if (origin is SubmissionOrigin.PAPER) != (caller.installation_id is not None):
            raise AuthenticationError
    return AuthenticatedSubmissionActor(
        caller.account_id,
        origin,
        java_uuid=caller.java_uuid,
        installation_id=caller.installation_id,
        grant_id=caller.grant_id,
    )


Forms = Annotated[SubmissionFormReader, Depends(get_submission_forms)]
Drafts = Annotated[SubmissionDraftCommands, Depends(get_submission_drafts)]
Finalization = Annotated[SubmissionFinalizationCommands, Depends(get_submission_finalization)]
AccountId = Annotated[int, Depends(authenticated_account)]
SubmissionActor = Annotated[AuthenticatedSubmissionActor, Depends(authenticated_submission_actor)]
OptionSource = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Category = Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SchemaId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")]

router = APIRouter(
    prefix="/submissions",
    tags=["submissions"],
    dependencies=[Depends(enforce_request_idempotency)],
)

_DRAFT_CREATE_LINKS = {
    "GetCreatedDraft": {
        "operationId": "submission_draft_get",
        "parameters": {"draft_id": "$response.body#/id"},
    },
    "ChangeCreatedDraft": {
        "operationId": "submission_draft_change",
        "parameters": {"draft_id": "$response.body#/id"},
    },
    "FinalizeCreatedDraft": {
        "operationId": "submission_finalization_start",
        "parameters": {"draft_id": "$response.body#/id"},
    },
    "DeleteCreatedDraft": {
        "operationId": "submission_draft_delete",
        "parameters": {"draft_id": "$response.body#/id"},
    },
}
_DRAFT_CHANGE_LINKS = {
    "ChangeDraftAgain": {
        "operationId": "submission_draft_change",
        "parameters": {"draft_id": "$response.body#/draft/id"},
    },
    "FinalizeChangedDraft": {
        "operationId": "submission_finalization_start",
        "parameters": {"draft_id": "$response.body#/draft/id"},
    },
}
_DRAFT_SUBMIT_LINKS = {
    "GetFinalization": {
        "operationId": "submission_finalization_get",
        "parameters": {"draft_id": "$response.body#/draft_id"},
    },
    "DeleteFinalizedDraft": {
        "operationId": "submission_draft_delete",
        "parameters": {"draft_id": "$response.body#/draft_id"},
    },
}
_DRAFT_DELETE_LINKS = {
    "UseAfterDeletedDraft": {
        "operationId": "submission_draft_get",
        "parameters": {"draft_id": "$request.path.draft_id"},
        "description": "Use-after-free check for a deleted draft identifier.",
    },
}


@router.get(
    "/drafts",
    response_model=DraftListResponse,
    responses=responses(401, 403, 503),
    operation_id="submission_drafts_list",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.list", features=("submission-drafts",), interaction="direct"),
    ),
)
async def list_drafts(drafts: Drafts, account_id: AccountId) -> DraftListResponse:
    """Return at most ten compact active drafts owned by the signed-in account."""
    return DraftListResponse.from_domain(await drafts.list_active(account_id))


@router.get(
    "/form/current",
    response_model=FormManifestResponse,
    responses=responses(503),
    operation_id="submission_form_current",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def current_form(request: Request, forms: Forms) -> FormManifestResponse:
    """Return the localized form and protocol bounds authored by this server."""
    manifest = forms.manifest(locale=locale_for_request(request))
    return FormManifestResponse.from_domain(manifest)


@router.get(
    "/form/schemas/{schema_id}/revisions/{revision}",
    response_model=FormManifestResponse,
    responses=responses(404, 422, 503),
    operation_id="submission_form_revision_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def pinned_form(
    schema_id: SchemaId,
    revision: Annotated[int, Path(ge=1)],
    request: Request,
    forms: Forms,
) -> FormManifestResponse:
    """Return an exact immutable schema revision retained for an unexpired draft."""
    manifest = await forms.manifest_revision(schema_id, revision, locale=locale_for_request(request))
    if manifest is None:
        raise SubmissionFormRevisionNotFoundError
    return FormManifestResponse.from_domain(manifest)


@router.get(
    "/form/options/{source}",
    response_model=FormOptionSetResponse,
    responses=responses(400, 404, 422, 503),
    operation_id="submission_form_options_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def form_options(
    source: OptionSource,
    category: Category,
    request: Request,
    forms: Forms,
) -> FormOptionSetResponse:
    """Return one revisioned dynamic option source for a build category."""
    option_set = await forms.options(source, category, locale=locale_for_request(request))
    return FormOptionSetResponse.from_domain(option_set)


@router.post(
    "/drafts",
    response_model=StoredDraftResponse,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="submission_draft_create",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.create", features=("submission-drafts",), interaction="direct"),
        links={"201": _DRAFT_CREATE_LINKS},
    ),
)
async def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    drafts: Drafts,
    actor: SubmissionActor,
) -> StoredDraftResponse:
    """Create an empty synchronized draft owned by the signed-in account."""
    if payload.origin is not actor.origin:
        raise AuthorizationError
    draft = await drafts.create(
        owner_account_id=actor.account_id,
        category=payload.category,
        origin=actor.origin,
        client_capabilities=frozenset(payload.client_capabilities),
        locale=locale_for_request(request),
        source_installation_id=actor.installation_id,
    )
    return StoredDraftResponse.from_domain(draft)


@router.get(
    "/drafts/{draft_id}",
    response_model=StoredDraftResponse,
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_get",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.show", features=("submission-drafts",), interaction="direct"),
    ),
)
async def get_draft(draft_id: UUID, drafts: Drafts, account_id: AccountId) -> StoredDraftResponse:
    """Return one synchronized draft after enforcing caller ownership."""
    return StoredDraftResponse.from_domain(await drafts.get_owned(draft_id, account_id))


@router.post(
    "/drafts/{draft_id}/changes",
    response_model=DraftChangeResponse,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_draft_change",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.change", features=("submission-drafts",), interaction="direct"),
        links={"200": _DRAFT_CHANGE_LINKS},
    ),
)
async def change_draft(
    draft_id: UUID,
    payload: DraftChangeRequest,
    request: Request,
    drafts: Drafts,
    account_id: AccountId,
) -> DraftChangeResponse:
    """Atomically apply a retry-safe optimistic edit to an owned draft."""
    result = await drafts.apply_change(
        draft_id,
        account_id,
        payload.to_domain(),
        locale=locale_for_request(request),
    )
    return DraftChangeResponse(draft=StoredDraftResponse.from_domain(result.draft), replayed=result.replayed)


@router.post(
    "/drafts/{draft_id}/submission",
    response_model=SubmissionFinalizationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_finalization_start",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.submit", features=("submission-finalization",), interaction="direct"),
        links={"202": _DRAFT_SUBMIT_LINKS},
    ),
)
async def submit_draft(
    draft_id: UUID,
    request: Request,
    finalization: Finalization,
    account_id: AccountId,
) -> SubmissionFinalizationResponse:
    """Validate an owned draft and start retry-safe durable finalization."""
    snapshot = await finalization.submit(
        draft_id,
        account_id,
        locale=locale_for_request(request),
    )
    return SubmissionFinalizationResponse.from_domain(snapshot)


@router.get(
    "/drafts/{draft_id}/submission",
    response_model=SubmissionFinalizationResponse,
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_finalization_get",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.status", features=("submission-finalization",), interaction="direct"),
    ),
)
async def get_draft_submission(
    draft_id: UUID,
    finalization: Finalization,
    account_id: AccountId,
) -> SubmissionFinalizationResponse:
    """Return retained finalization state after rechecking draft ownership."""
    snapshot = await finalization.status(draft_id, account_id)
    if snapshot is None:
        raise SubmissionFinalizationNotFoundError
    return SubmissionFinalizationResponse.from_domain(snapshot)


@router.delete(
    "/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_delete",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.delete", features=("submission-drafts",), interaction="direct"),
        links={"204": _DRAFT_DELETE_LINKS},
    ),
)
async def delete_draft(draft_id: UUID, drafts: Drafts, account_id: AccountId) -> Response:
    """Immediately delete one caller-owned synchronized draft."""
    await drafts.delete(draft_id, account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
