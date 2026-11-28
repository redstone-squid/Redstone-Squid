"""Submission form and draft routes.

The form manifest describes fields and constraints; whether they become a
Discord modal, an HTML form, or a CLI prompt is the client's decision. A draft
stores answers against a particular manifest revision. Submitting it either
reports required corrections or processing waits, or starts a durable
finalization attempt that produces a build.
"""

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic, time
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from litestar import Request, Response, Router, delete, get, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import (
    FromPath,
    FromQuery,
    HeaderParameter,
    JSONBody,
    PathParameter,
    QueryParameter,
    SkipValidation,
)
from litestar.serialization import encode_json

from squid.api.contract import ANONYMOUS, DEVICE, MINECRAFT, WEB, WEB_WRITE, cli_command, contract, transport_only
from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.idempotency import (
    edit_operation_id,
    reserve_edit_before_request,
    reserve_edit_request,
    reserve_idempotent_request,
)
from squid.api.pagination import (
    CursorParam,
    OffsetParam,
    Page,
    PageSizeParam,
    resolve_selector,
)
from squid.api.private_responses import private_response
from squid.api.revisions import InvalidRevisionError, expected_revision, revision_etag
from squid.api.security import Caller, current_caller
from squid.api.v1.schemas.submissions import (
    DraftChangeRequest,
    DraftChangeResponse,
    DraftCreateRequest,
    DraftListResponse,
    DraftManifestUpgradeRequest,
    DraftManifestUpgradeResponse,
    DraftPreparationResponse,
    DraftSummaryResponse,
    FormManifestResponse,
    FormOptionSetResponse,
    StoredDraftResponse,
    SubmissionAttemptResponse,
)
from squid.core.actors import ActingContext
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import Revision
from squid.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from squid.core.observability import record_histogram
from squid.core.pagination import FIRST_PAGE, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.records.application.preview import RecordPreview
from squid.core.submissions.application import (
    AppliedDraftUpgrade,
    DraftPreparationSnapshot,
    FinalizationJobSnapshot,
    FormOptionSet,
    StoredDraft,
    SubmissionRequestResult,
)
from squid.core.submissions.application.edits import change_command, draft_target
from squid.core.submissions.application.finalization import DraftEditor
from squid.core.submissions.application.reviews import SubmissionDetail, SubmissionStatus, SubmissionSummary
from squid.core.submissions.domain import FormManifest, FormProgress, SubmissionOrigin
from squid.core.submissions.domain.build_edit import DraftEditResult


class SubmissionFormReader(Protocol):
    """Form reads needed by the HTTP transport."""

    async def manifest(self, *, locale: str | None, context: ActingContext) -> FormManifest: ...

    async def manifest_revision(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
        context: ActingContext,
    ) -> FormManifest | None: ...

    async def options(
        self, source: str, category: str, *, locale: str | None, context: ActingContext
    ) -> FormOptionSet: ...


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
        context: ActingContext,
    ) -> StoredDraft: ...

    async def list_active(self, *, limit: int = 10, context: ActingContext) -> tuple[StoredDraft, ...]: ...

    async def attention_inbox(
        self, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> ResultPage[StoredDraft]: ...

    async def get_accessible(self, draft_id: UUID, *, context: ActingContext) -> StoredDraft: ...

    async def progress(self, draft: StoredDraft) -> FormProgress | None: ...

    async def upgrade_manifest(
        self,
        draft_id: UUID,
        *,
        expected_revision: int,
        target_revision: int,
        locale: str | None,
        context: ActingContext,
    ) -> AppliedDraftUpgrade: ...

    async def delete(self, draft_id: UUID, *, context: ActingContext) -> None: ...


class SubmissionFinalizationCommands(Protocol):
    """Owner-scoped finalization operations needed by the HTTP transport."""

    @property
    def editor(self) -> DraftEditor | None: ...

    async def submit(
        self,
        draft_id: UUID,
        *,
        context: ActingContext,
        locale: str | None,
    ) -> SubmissionRequestResult | DraftEditResult: ...

    async def status(
        self, draft_id: UUID, *, context: ActingContext
    ) -> SubmissionRequestResult | DraftEditResult | None: ...

    async def attempt(
        self, draft_id: UUID, attempt_id: UUID, *, context: ActingContext
    ) -> FinalizationJobSnapshot | None: ...

    async def attempts(
        self, draft_id: UUID, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> ResultPage[FinalizationJobSnapshot]: ...


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


async def authenticated_submission_context(caller: NamedDependency[SkipValidation[Caller]]) -> ActingContext:
    """Require a current human or player-bound account for draft access."""
    _submission_origin(caller)
    return caller.context()


async def authenticated_submission_caller(request: Request) -> Caller:
    """Resolve submission origin without trusting a request-body origin."""
    caller = await current_caller(request)
    _submission_origin(caller)
    return caller


def _submission_origin(caller: Caller) -> SubmissionOrigin:
    """Derive submission origin from authenticated credentials.

    Only browser, CLI, and Minecraft player credentials may submit. CLI and
    Minecraft identities must include the identifiers required for their origin;
    a Paper player also needs an installation ID.
    """
    if caller.account_id is None or caller.kind not in {"account", "cli", "minecraft_player"}:
        raise AuthenticationError
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
SubmissionContext = NamedDependency[SkipValidation[ActingContext]]
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
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
async def list_correction_inbox(
    drafts: Drafts,
    context: SubmissionContext,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
) -> Page[DraftSummaryResponse]:
    """List drafts needing staff correction oldest first, excluding attachment processing waits."""
    page = await drafts.attention_inbox(
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
        context=context,
    )
    progress = {draft.snapshot.id: await drafts.progress(draft) for draft in page.items}
    return Page[DraftSummaryResponse].from_domain(
        page, lambda draft: DraftSummaryResponse.from_domain(draft, progress[draft.snapshot.id])
    )


@get(
    "/drafts",
    responses=responses(401, 403, 503),
    operation_id="submission_drafts_list",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.list", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def list_drafts(drafts: Drafts, context: SubmissionContext) -> DraftListResponse:
    """Return at most ten compact active drafts owned by the signed-in account."""
    active = await drafts.list_active(context=context)
    return DraftListResponse(
        drafts=[DraftSummaryResponse.from_domain(draft, await drafts.progress(draft)) for draft in active]
    )


@get(
    "/form/current",
    responses=responses(503),
    operation_id="submission_form_current",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def current_form(request: Request, forms: Forms, caller: SubmissionCaller) -> FormManifestResponse:
    """Return the localized form and protocol bounds authored by this server."""
    manifest = await forms.manifest(locale=locale_for_request(request), context=caller.context())
    return FormManifestResponse.from_domain(manifest)


@get(
    "/form/schemas/{schema_id:str}/revisions/{revision:str}",
    responses=responses(404, 422, 503),
    operation_id="submission_form_revision_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def pinned_form(
    schema_id: SchemaId,
    revision: Annotated[int, PathParameter(ge=1)],
    request: Request,
    forms: Forms,
    caller: SubmissionCaller,
) -> FormManifestResponse:
    """Return an exact immutable schema revision retained for an unexpired draft."""
    manifest = await forms.manifest_revision(
        schema_id, revision, locale=locale_for_request(request), context=caller.context()
    )
    if manifest is None:
        raise SubmissionFormRevisionNotFoundError
    return FormManifestResponse.from_domain(manifest)


@get(
    "/form/options/{source:str}",
    responses=responses(400, 404, 422, 503),
    operation_id="submission_form_options_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={"forms": Provide(get_submission_forms, sync_to_thread=False)},
)
async def form_options(
    source: OptionSource, category: Category, request: Request, forms: Forms, caller: SubmissionCaller
) -> FormOptionSetResponse:
    """Return one revisioned dynamic option source for a build category."""
    option_set = await forms.options(source, category, locale=locale_for_request(request), context=caller.context())
    return FormOptionSetResponse.from_domain(option_set)


@post(
    "/drafts",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_draft_create",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.create", features=("submission-drafts",), interaction="direct"),
        links={"201": _DRAFT_CREATE_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "caller": Provide(authenticated_submission_caller),
    },
)
@private_response
async def create_draft(
    data: JSONBody[DraftCreateRequest],
    request: Request,
    drafts: Drafts,
    finalization: Finalization,
    caller: SubmissionCaller,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> StoredDraftResponse:
    """Create a submission draft or prefill an edit draft from an approved source build."""
    origin = _submission_origin(caller)
    if data.origin is not origin:
        raise AuthorizationError
    if data.source_build_id is not None:
        editor = finalization.editor
        if origin is not SubmissionOrigin.WEB or editor is None:
            raise AuthorizationError
        draft = await editor.create(
            data.source_build_id,
            data.category,
            frozenset(data.client_capabilities),
            locale_for_request(request),
            context=caller.context(),
        )
        return StoredDraftResponse.from_domain(draft, await drafts.progress(draft), caller.require_account_id())
    draft = await drafts.create(
        owner_account_id=caller.require_account_id(),
        context=caller.context(),
        category=data.category,
        origin=origin,
        client_capabilities=frozenset(data.client_capabilities),
        locale=locale_for_request(request),
        source_installation_id=caller.installation_id,
    )
    return StoredDraftResponse.from_domain(draft, await drafts.progress(draft), caller.require_account_id())


@get(
    "/drafts/{draft_id:uuid}",
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_get",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.show", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def get_draft(
    draft_id: FromPath[UUID], drafts: Drafts, context: SubmissionContext
) -> Response[StoredDraftResponse]:
    """Return a draft to its owner or an authorized correction editor."""
    response_headers: dict[str, str] = {}
    draft = await drafts.get_accessible(draft_id, context=context)
    response_headers["ETag"] = revision_etag(Revision(draft_target(draft_id), draft.snapshot.revision))
    content = StoredDraftResponse.from_domain(
        draft, await drafts.progress(draft), context.effective.require_account_id()
    )
    return Response(content=content, headers=response_headers, status_code=200)


@post(
    "/drafts/{draft_id:uuid}/changes",
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    operation_id="submission_draft_change",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.change", features=("submission-drafts",), interaction="direct"),
        links={"200": _DRAFT_CHANGE_LINKS},
    ),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
        "edits": Provide(get_submission_edits, sync_to_thread=False),
    },
)
@private_response
async def change_draft(
    draft_id: FromPath[UUID],
    data: JSONBody[DraftChangeRequest],
    drafts: Drafts,
    context: SubmissionContext,
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
    result = await edits.execute(command, context=context)
    draft = await drafts.get_accessible(draft_id, context=context)
    response_headers["ETag"] = revision_etag(Revision(draft_target(draft_id), draft.snapshot.revision))
    return Response(
        content=DraftChangeResponse(
            draft=StoredDraftResponse.from_domain(
                draft, await drafts.progress(draft), context.effective.require_account_id()
            ),
            replayed=result.replayed,
        ),
        headers=response_headers,
        status_code=200,
    )


@post(
    "/drafts/{draft_id:uuid}/manifest-upgrade",
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_draft_manifest_upgrade",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.upgrade", features=("submission-drafts",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def upgrade_draft_manifest(
    draft_id: FromPath[UUID],
    data: JSONBody[DraftManifestUpgradeRequest],
    request: Request,
    drafts: Drafts,
    context: SubmissionContext,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> DraftManifestUpgradeResponse:
    """Upgrade an owned draft to the next checked-in form revision."""
    result = await drafts.upgrade_manifest(
        draft_id,
        context=context,
        expected_revision=data.base_revision,
        target_revision=data.target_revision,
        locale=locale_for_request(request),
    )
    draft = StoredDraftResponse.from_domain(
        result.draft, await drafts.progress(result.draft), context.effective.require_account_id()
    )
    return DraftManifestUpgradeResponse(draft=draft, replayed=result.replayed)


@post(
    "/drafts/{draft_id:uuid}/attempts",
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    operation_id="submission_finalization_start",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.submit", features=("submission-finalization",), interaction="direct"),
        links={"202": _DRAFT_SUBMIT_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def submit_draft(
    draft_id: FromPath[UUID],
    request: Request,
    finalization: Finalization,
    context: SubmissionContext,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> SubmissionAttemptResponse | DraftPreparationResponse | DraftEditResult:
    """Validate a draft and request finalization.

    If files are still processing or fields need correction, return preparation
    state. Otherwise return the durable attempt that performs finalization.
    """
    snapshot = await finalization.submit(draft_id, context=context, locale=locale_for_request(request))
    if isinstance(snapshot, DraftEditResult):
        return snapshot
    if isinstance(snapshot, DraftPreparationSnapshot):
        return DraftPreparationResponse.from_domain(snapshot)
    return SubmissionAttemptResponse.from_domain(snapshot)


@get(
    "/drafts/{draft_id:uuid}/status",
    responses={
        **responses(401, 403, 404, 422, 503),
        200: ResponseSpec(
            SubmissionAttemptResponse | DraftPreparationResponse | DraftEditResult,
            generate_examples=False,
            description="Current finalization status.",
        ),
        304: ResponseSpec(None, description="Finalization status is unchanged."),
    },
    operation_id="submission_finalization_get",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.status", features=("submission-finalization",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def get_draft_submission(
    draft_id: FromPath[UUID],
    finalization: Finalization,
    context: SubmissionContext,
    if_none_match: Annotated[str | None, HeaderParameter(name="If-None-Match")] = None,
) -> Response[bytes]:
    """Return the current attempt or a conditional response after checking ownership."""
    started = monotonic()
    snapshot = await finalization.status(draft_id, context=context)
    if snapshot is None:
        raise SubmissionFinalizationNotFoundError
    if isinstance(snapshot, DraftEditResult):
        result = snapshot
    elif isinstance(snapshot, DraftPreparationSnapshot):
        result = DraftPreparationResponse.from_domain(snapshot)
    else:
        result = SubmissionAttemptResponse.from_domain(snapshot)
    body = encode_json(result if isinstance(result, DraftEditResult) else result.model_dump(mode="json"))
    etag = f'"{sha256(body).hexdigest()}"'
    headers = {"ETag": etag}
    unchanged = if_none_match is not None and any(
        tag.strip().removeprefix("W/") in {etag, "*"} for tag in if_none_match.split(",")
    )
    record_histogram(
        "squid.submissions.status.read_seconds",
        monotonic() - started,
        attributes={"conditional": if_none_match is not None, "unchanged": unchanged},
    )
    if unchanged:
        return Response(status_code=304, content=b"", headers=headers)
    if if_none_match and isinstance(snapshot, FinalizationJobSnapshot) and snapshot.completed_at is not None:
        record_histogram(
            "squid.submissions.status.completion_observed_seconds",
            max(0.0, time() - snapshot.completed_at.to_stdlib().timestamp()),
        )
    return Response(content=body, headers=headers, media_type="application/json")


@get(
    "/drafts/{draft_id:uuid}/attempts",
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="submission_attempts_list",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def list_draft_attempts(
    draft_id: FromPath[UUID],
    finalization: Finalization,
    context: SubmissionContext,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
) -> Page[SubmissionAttemptResponse]:
    """List retained attempts newest first."""
    page = await finalization.attempts(
        draft_id,
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
        context=context,
    )
    return Page[SubmissionAttemptResponse].from_domain(page, SubmissionAttemptResponse.from_domain)


@get(
    "/drafts/{draft_id:uuid}/attempts/{attempt_id:uuid}",
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="submission_attempt_get",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
    status_code=200,
    dependencies={
        "finalization": Provide(get_submission_finalization, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def get_draft_attempt(
    draft_id: FromPath[UUID], attempt_id: FromPath[UUID], finalization: Finalization, context: SubmissionContext
) -> SubmissionAttemptResponse:
    """Read one attempt without exposing execution credentials or its private payload."""
    snapshot = await finalization.attempt(draft_id, attempt_id, context=context)
    if snapshot is None:
        raise SubmissionFinalizationNotFoundError
    return SubmissionAttemptResponse.from_domain(snapshot)


@delete(
    "/drafts/{draft_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 422, 503),
    operation_id="submission_draft_delete",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("draft.delete", features=("submission-drafts",), interaction="direct"),
        links={"204": _DRAFT_DELETE_LINKS},
    ),
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "drafts": Provide(get_submission_drafts, sync_to_thread=False),
        "context": Provide(authenticated_submission_context),
    },
)
@private_response
async def delete_draft(
    draft_id: FromPath[UUID],
    drafts: Drafts,
    context: SubmissionContext,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Immediately delete one caller-owned draft."""
    await drafts.delete(draft_id, context=context)
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


async def submission_reader(caller: NamedDependency[SkipValidation[Caller]]) -> ActingContext:
    """Use browser session authority for account portfolio and reviewer reads."""
    if caller.kind != "account":
        raise AuthenticationError
    return caller.context()


@get(
    "/",
    dependencies={"context": Provide(submission_reader)},
    operation_id="submissions_list",
    security=[WEB],
    opt=contract(cli=transport_only()),
    responses=responses(401, 403, 422, 503),
)
@private_response
async def list_submissions(
    services: Services,
    context: SubmissionContext,
    status: FromQuery[SubmissionStatus | None] = None,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 20,
) -> Page[SubmissionSummary]:
    """List the signed-in account's submissions, newest first."""
    page = await services.viewer_queries.submissions(
        resolve_selector(offset=offset, cursor=cursor), page_size, status=status, context=context
    )
    return Page.from_domain(page)


@get(
    "/{submission_id:uuid}",
    dependencies={"context": Provide(submission_reader)},
    operation_id="submissions_get",
    security=[WEB],
    opt=contract(cli=transport_only()),
    responses=responses(401, 403, 404, 422, 503),
)
@private_response
async def get_submission(
    submission_id: FromPath[UUID],
    services: Services,
    context: SubmissionContext,
    review_session_id: FromQuery[int | None] = None,
) -> SubmissionDetail:
    """Open one submission as its submitter or an authorized reviewer."""
    return await services.viewer_queries.submission(submission_id, context=context, review_session_id=review_session_id)


@get(
    "/review-queue",
    operation_id="submissions_review_queue",
    security=[WEB],
    opt=contract(cli=transport_only()),
    responses=responses(401, 403, 422, 503),
    dependencies={"context": Provide(submission_reader)},
)
@private_response
async def submission_review_queue(
    services: Services,
    context: SubmissionContext,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
    page_size: PageSizeParam = 20,
) -> Page[SubmissionSummary]:
    """List pending new builds and edits for authorized reviewers, oldest first."""
    return Page.from_domain(
        await services.viewer_queries.review_queue(
            resolve_selector(offset=offset, cursor=cursor), page_size, context=context
        )
    )


@dataclass
class SubmissionDecision:
    revision: int
    decision: Literal["approve", "reject"]
    feedback: str | None = None


@post(
    "/{submission_id:uuid}/decision",
    operation_id="submissions_decide",
    security=[WEB_WRITE],
    opt=contract(cli=transport_only()),
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    status_code=200,
    before_request=reserve_edit_before_request,
    dependencies={
        "context": Provide(submission_reader),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
@private_response
async def decide_submission(
    submission_id: FromPath[UUID],
    data: JSONBody[SubmissionDecision],
    services: Services,
    context: SubmissionContext,
    operation_id: NamedDependency[UUID],
) -> SubmissionDetail:
    """Apply a review decision to the displayed candidate revision."""
    return await services.submission_reviews.decide(
        submission_id, data.revision, data.decision, data.feedback, operation_id, context=context
    )


@get(
    "/drafts/{draft_id:uuid}/record-preview",
    operation_id="submission_draft_record_preview",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
    responses=responses(401, 403, 404, 422, 503),
    dependencies={"context": Provide(authenticated_submission_context)},
)
@private_response
async def draft_record_preview(
    draft_id: FromPath[UUID], services: Services, context: SubmissionContext
) -> RecordPreview:
    """Predict records for the saved draft revision."""
    return await services.record_previews.draft(draft_id, context=context)


@get(
    "/{submission_id:uuid}/record-preview",
    operation_id="submission_record_preview",
    security=[WEB],
    opt=contract(cli=transport_only()),
    responses=responses(401, 403, 404, 422, 503),
    dependencies={"context": Provide(submission_reader)},
)
@private_response
async def submission_record_preview(
    submission_id: FromPath[UUID],
    services: Services,
    context: SubmissionContext,
    review_session_id: FromQuery[int | None] = None,
) -> RecordPreview:
    """Compare the reviewed revision with the current approved population."""
    return await services.record_previews.submission(submission_id, review_session_id, context=context)


router = Router(
    path="/submissions",
    tags=["submissions"],
    route_handlers=[
        draft_record_preview,
        submission_record_preview,
        decide_submission,
        submission_review_queue,
        list_submissions,
        get_submission,
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
