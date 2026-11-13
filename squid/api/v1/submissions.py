"""Submission form and draft routes.

The form manifest describes fields and constraints; whether they become a
Discord modal, an HTML form, or a CLI prompt is the client's decision. A draft
stores answers against a particular manifest revision. Submitting it either
reports required corrections or processing waits, or starts a durable
finalization attempt that produces a build.
"""

from typing import Annotated, Protocol, cast
from uuid import UUID

from litestar import Request, Response, Router, delete, get, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import HeaderParameter, JSONBody, PathParameter, QueryParameter, SkipValidation

from squid.api.contract import ANONYMOUS, DEVICE, MINECRAFT, WEB, WEB_WRITE, cli_command, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.idempotency import reserve_edit_request, reserve_idempotent_request
from squid.api.revisions import InvalidRevisionError, expected_revision, revision_etag
from squid.api.security import Caller, current_caller
from squid.api.v1.schemas.submissions import (
    DraftChangeRequest,
    DraftChangeResponse,
    DraftCreateRequest,
    DraftInboxResponse,
    DraftListResponse,
    DraftManifestUpgradeRequest,
    DraftManifestUpgradeResponse,
    DraftPreparationResponse,
    FormManifestResponse,
    FormOptionSetResponse,
    StoredDraftResponse,
    SubmissionAttemptResponse,
)
from squid.core.actors import Actor
from squid.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from squid.edits.application import EditRunner
from squid.edits.domain import Revision
from squid.submissions.application import (
    AppliedDraftUpgrade,
    DraftPreparationSnapshot,
    FinalizationJobSnapshot,
    FormOptionSet,
    StoredDraft,
    SubmissionRequestResult,
)
from squid.submissions.application.edits import change_command, draft_target
from squid.submissions.domain import FormManifest, SubmissionOrigin


class SubmissionFormReader(Protocol):
    """Form reads needed by the HTTP transport."""

    async def manifest(self, *, locale: str | None) -> FormManifest: ...

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

    async def attention_inbox(
        self,
        actor: int,
        *,
        after: UUID | None = None,
        limit: int = 20,
    ) -> tuple[StoredDraft, ...]: ...

    async def get_accessible(self, draft_id: UUID, account_id: int) -> StoredDraft: ...

    async def upgrade_manifest(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        target_revision: int,
        locale: str | None,
    ) -> AppliedDraftUpgrade: ...

    async def delete(self, draft_id: UUID, account_id: int) -> None: ...


class SubmissionFinalizationCommands(Protocol):
    """Owner-scoped finalization operations needed by the HTTP transport."""

    async def submit(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        locale: str | None,
    ) -> SubmissionRequestResult: ...

    async def status(self, draft_id: UUID, account_id: int) -> SubmissionRequestResult | None: ...

    async def attempt(self, draft_id: UUID, account_id: int, attempt_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def attempts(
        self, draft_id: UUID, account_id: int, *, before: int | None = None, limit: int = 20
    ) -> tuple[FinalizationJobSnapshot, ...]: ...


class SubmissionApiServices(Protocol):
    """Narrow runtime service bundle consumed by this router."""

    edits: EditRunner
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


def get_submission_edits(request: Request) -> EditRunner:
    state = cast(_SubmissionAppState, request.app.state)
    return state.runtime.services.edits


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


async def authenticated_account(caller: NamedDependency[SkipValidation[Caller]]) -> int:
    """Require a current human or player-bound account for draft access."""
    _submission_origin(caller)
    return caller.require_consented_account()


async def authenticated_submission_caller(request: Request) -> Caller:
    """Resolve submission provenance without trusting a request-body origin."""
    caller = await current_caller(request)
    _submission_origin(caller)
    return caller


def _submission_origin(caller: Caller) -> SubmissionOrigin:
    """Derive submission origin from authenticated credentials and check consent.

    Only browser, CLI, and Minecraft player credentials may submit. CLI and
    Minecraft identities must include the identifiers required for their origin;
    a Paper player also needs an installation ID.
    """
    if caller.account_id is None or caller.kind not in {"account", "cli", "minecraft_player"}:
        raise AuthenticationError
    caller.require_consented_account()
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
    return origin


Forms = NamedDependency[SkipValidation[SubmissionFormReader]]
Drafts = NamedDependency[SkipValidation[SubmissionDraftCommands]]
Finalization = NamedDependency[SkipValidation[SubmissionFinalizationCommands]]
AccountId = NamedDependency[SkipValidation[int]]
SubmissionCaller = NamedDependency[SkipValidation[Caller]]
OptionSource = Annotated[str, PathParameter(pattern="^[a-z][a-z0-9_]{0,63}$")]
Category = Annotated[str, QueryParameter(pattern="^[a-z][a-z0-9_]{0,63}$")]
SchemaId = Annotated[str, PathParameter(pattern="^[a-z][a-z0-9_.-]{0,95}$")]


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


@get(
    "/inbox",
    responses=responses(401, 403, 422, 503),
    operation_id="submission_correction_inbox",
    opt=contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only()),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def list_correction_inbox(
    drafts: Drafts,
    account_id: AccountId,
    after: UUID | None = None,
    limit: Annotated[int, QueryParameter(ge=1, le=100)] = 20,
) -> DraftInboxResponse:
    """List drafts needing staff correction, excluding attachment processing waits."""
    return DraftInboxResponse.from_domain(await drafts.attention_inbox(account_id, after=after, limit=limit))


@get(
    "/drafts",
    responses=responses(401, 403, 503),
    operation_id="submission_drafts_list",
    opt=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.list", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def list_drafts(drafts: Drafts, account_id: AccountId) -> DraftListResponse:
    """Return at most ten compact active drafts owned by the signed-in account."""
    return DraftListResponse.from_domain(await drafts.list_active(account_id))


@get(
    "/form/current",
    responses=responses(503),
    operation_id="submission_form_current",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def current_form(request: Request, forms: Forms) -> FormManifestResponse:
    """Return the localized form and protocol bounds authored by this server."""
    manifest = await forms.manifest(locale=locale_for_request(request))
    return FormManifestResponse.from_domain(manifest)


@get(
    "/form/schemas/{schema_id:str}/revisions/{revision:str}",
    responses=responses(404, 422, 503),
    operation_id="submission_form_revision_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def pinned_form(
    schema_id: SchemaId, revision: Annotated[int, PathParameter(ge=1)], request: Request, forms: Forms
) -> FormManifestResponse:
    """Return an exact immutable schema revision retained for an unexpired draft."""
    manifest = await forms.manifest_revision(schema_id, revision, locale=locale_for_request(request))
    if manifest is None:
        raise SubmissionFormRevisionNotFoundError
    return FormManifestResponse.from_domain(manifest)


@get(
    "/form/options/{source:str}",
    responses=responses(400, 404, 422, 503),
    operation_id="submission_form_options_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def form_options(
    source: OptionSource, category: Category, request: Request, forms: Forms
) -> FormOptionSetResponse:
    """Return one revisioned dynamic option source for a build category."""
    option_set = await forms.options(source, category, locale=locale_for_request(request))
    return FormOptionSetResponse.from_domain(option_set)


@post(
    "/drafts",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="submission_draft_create",
    opt=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.create", features=("submission-drafts",), interaction="direct"),
        links={"201": _DRAFT_CREATE_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "caller": Provide(authenticated_submission_caller),
    },
)
async def create_draft(
    data: JSONBody[DraftCreateRequest],
    request: Request,
    drafts: Drafts,
    caller: SubmissionCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> StoredDraftResponse:
    """Create an empty draft owned by the signed-in account."""
    origin = _submission_origin(caller)
    if data.origin is not origin:
        raise AuthorizationError
    draft = await drafts.create(
        owner_account_id=caller.require_consented_account(),
        category=data.category,
        origin=origin,
        client_capabilities=frozenset(data.client_capabilities),
        locale=locale_for_request(request),
        source_installation_id=caller.installation_id,
    )
    return StoredDraftResponse.from_domain(draft)


@get(
    "/drafts/{draft_id:uuid}",
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_get",
    opt=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.show", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def get_draft(draft_id: UUID, drafts: Drafts, account_id: AccountId) -> Response[StoredDraftResponse]:
    """Return one draft after enforcing caller ownership."""
    response_headers: dict[str, str] = {}
    draft = await drafts.get_accessible(draft_id, account_id)
    response_headers["ETag"] = revision_etag(Revision(draft_target(draft_id), draft.snapshot.revision))
    return Response(content=StoredDraftResponse.from_domain(draft), headers=response_headers, status_code=200)


@post(
    "/drafts/{draft_id:uuid}/changes",
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    operation_id="submission_draft_change",
    opt=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.change", features=("submission-drafts",), interaction="direct"),
        links={"200": _DRAFT_CHANGE_LINKS},
    ),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
        "edits": Provide(get_submission_edits, sync_to_thread=False),
    },
)
async def change_draft(
    draft_id: UUID,
    data: JSONBody[DraftChangeRequest],
    drafts: Drafts,
    account_id: AccountId,
    edits: NamedDependency[SkipValidation[EditRunner]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[DraftChangeResponse]:
    """Apply an owned draft change; an optional If-Match must agree with base_revision."""
    response_headers: dict[str, str] = {}
    command = change_command(draft_id, data.to_domain())
    if if_match is not None and expected_revision(draft_target(draft_id), if_match) != command.expected[0]:
        raise InvalidRevisionError
    result = await edits.execute(command, Actor(account_id=account_id))
    draft = await drafts.get_accessible(draft_id, account_id)
    response_headers["ETag"] = revision_etag(Revision(draft_target(draft_id), draft.snapshot.revision))
    return Response(
        content=DraftChangeResponse(draft=StoredDraftResponse.from_domain(draft), replayed=result.replayed),
        headers=response_headers,
        status_code=200,
    )


@post(
    "/drafts/{draft_id:uuid}/manifest-upgrade",
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_draft_manifest_upgrade",
    opt=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.upgrade", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def upgrade_draft_manifest(
    draft_id: UUID,
    data: JSONBody[DraftManifestUpgradeRequest],
    request: Request,
    drafts: Drafts,
    account_id: AccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> DraftManifestUpgradeResponse:
    """Upgrade an owned draft to the next checked-in form revision."""
    result = await drafts.upgrade_manifest(
        draft_id,
        account_id,
        expected_revision=data.base_revision,
        target_revision=data.target_revision,
        locale=locale_for_request(request),
    )
    return DraftManifestUpgradeResponse.from_domain(result)


@post(
    "/drafts/{draft_id:uuid}/attempts",
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_finalization_start",
    opt=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.submit", features=("submission-finalization",), interaction="direct"),
        links={"202": _DRAFT_SUBMIT_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def submit_draft(
    draft_id: UUID,
    request: Request,
    finalization: Finalization,
    account_id: AccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> SubmissionAttemptResponse | DraftPreparationResponse:
    """Validate a draft and request finalization.

    If files are still processing or fields need correction, return preparation
    state. Otherwise return the durable attempt that performs finalization.
    """
    snapshot = await finalization.submit(draft_id, account_id, locale=locale_for_request(request))
    if isinstance(snapshot, DraftPreparationSnapshot):
        return DraftPreparationResponse.from_domain(snapshot)
    return SubmissionAttemptResponse.from_domain(snapshot)


@get(
    "/drafts/{draft_id:uuid}/status",
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_finalization_get",
    opt=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("draft.status", features=("submission-finalization",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def get_draft_submission(
    draft_id: UUID, finalization: Finalization, account_id: AccountId
) -> SubmissionAttemptResponse | DraftPreparationResponse:
    """Return retained finalization state after rechecking draft ownership."""
    snapshot = await finalization.status(draft_id, account_id)
    if snapshot is None:
        raise SubmissionFinalizationNotFoundError
    if isinstance(snapshot, DraftPreparationSnapshot):
        return DraftPreparationResponse.from_domain(snapshot)
    return SubmissionAttemptResponse.from_domain(snapshot)


@get(
    "/drafts/{draft_id:uuid}/attempts",
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="submission_attempts_list",
    opt=contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only()),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def list_draft_attempts(
    draft_id: UUID,
    finalization: Finalization,
    account_id: AccountId,
    before: Annotated[int | None, QueryParameter(ge=1)] = None,
    limit: Annotated[int, QueryParameter(ge=1, le=100)] = 20,
) -> list[SubmissionAttemptResponse]:
    """List retained attempts newest first; use the last attempt number as the next cursor."""
    snapshots = await finalization.attempts(draft_id, account_id, before=before, limit=limit)
    return [SubmissionAttemptResponse.from_domain(snapshot) for snapshot in snapshots]


@get(
    "/drafts/{draft_id:uuid}/attempts/{attempt_id:uuid}",
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="submission_attempt_get",
    opt=contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only()),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def get_draft_attempt(
    draft_id: UUID, attempt_id: UUID, finalization: Finalization, account_id: AccountId
) -> SubmissionAttemptResponse:
    """Read one attempt without exposing execution credentials or its private payload."""
    snapshot = await finalization.attempt(draft_id, account_id, attempt_id)
    if snapshot is None:
        raise SubmissionFinalizationNotFoundError
    return SubmissionAttemptResponse.from_domain(snapshot)


@delete(
    "/drafts/{draft_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_delete",
    opt=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("draft.delete", features=("submission-drafts",), interaction="direct"),
        links={"204": _DRAFT_DELETE_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "account_id": Provide(authenticated_account),
    },
)
async def delete_draft(
    draft_id: UUID, drafts: Drafts, account_id: AccountId, *, _reservation: NamedDependency[SkipValidation[None]]
) -> Response[None]:
    """Immediately delete one caller-owned draft."""
    await drafts.delete(draft_id, account_id)
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


router = Router(
    path="/submissions",
    tags=["submissions"],
    route_handlers=[
        list_correction_inbox,
        list_drafts,
        current_form,
        pinned_form,
        form_options,
        create_draft,
        get_draft,
        change_draft,
        upgrade_draft_manifest,
        submit_draft,
        get_draft_submission,
        list_draft_attempts,
        get_draft_attempt,
        delete_draft,
    ],
)
