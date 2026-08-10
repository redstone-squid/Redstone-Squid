"""Renderer-neutral submission form and synchronized-draft routes."""

from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.security import Principal, current_principal
from squid.api.v1.schemas.submissions import (
    DraftChangeRequest,
    DraftChangeResponse,
    DraftCreateRequest,
    FormManifestResponse,
    FormOptionSetResponse,
    StoredDraftResponse,
)
from squid.core.errors import AuthenticationError
from squid.submissions.application import AppliedDraftChange, FormOptionSet, StoredDraft
from squid.submissions.domain import DraftChange, FormManifest, SubmissionOrigin


class SubmissionFormReader(Protocol):
    """Form reads needed by the HTTP transport."""

    def manifest(self, *, locale: str | None) -> FormManifest: ...

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
    ) -> StoredDraft: ...

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


class SubmissionApiServices(Protocol):
    """Narrow runtime service bundle consumed by this router."""

    submission_forms: SubmissionFormReader
    submission_drafts: SubmissionDraftCommands


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


async def authenticated_account(
    principal: Annotated[Principal, Depends(current_principal)],
) -> int:
    """Require a signed-in human account for synchronized draft access."""
    if principal.kind != "account" or principal.account_id is None:
        raise AuthenticationError
    return principal.account_id


Forms = Annotated[SubmissionFormReader, Depends(get_submission_forms)]
Drafts = Annotated[SubmissionDraftCommands, Depends(get_submission_drafts)]
AccountId = Annotated[int, Depends(authenticated_account)]
OptionSource = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Category = Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.get(
    "/form/current",
    response_model=FormManifestResponse,
    responses=responses(503),
)
async def current_form(request: Request, forms: Forms) -> FormManifestResponse:
    """Return the localized form and protocol bounds authored by this server."""
    manifest = forms.manifest(locale=locale_for_request(request))
    return FormManifestResponse.from_domain(manifest)


@router.get(
    "/form/options/{source}",
    response_model=FormOptionSetResponse,
    responses=responses(400, 404, 422, 503),
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
)
async def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    drafts: Drafts,
    account_id: AccountId,
) -> StoredDraftResponse:
    """Create an empty synchronized draft owned by the signed-in account."""
    draft = await drafts.create(
        owner_account_id=account_id,
        category=payload.category,
        origin=payload.origin,
        client_capabilities=frozenset(payload.client_capabilities),
        locale=locale_for_request(request),
    )
    return StoredDraftResponse.from_domain(draft)


@router.get(
    "/drafts/{draft_id}",
    response_model=StoredDraftResponse,
    responses=responses(401, 403, 404, 422, 503),
)
async def get_draft(draft_id: UUID, drafts: Drafts, account_id: AccountId) -> StoredDraftResponse:
    """Return one synchronized draft after enforcing caller ownership."""
    return StoredDraftResponse.from_domain(await drafts.get_owned(draft_id, account_id))


@router.post(
    "/drafts/{draft_id}/changes",
    response_model=DraftChangeResponse,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
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


@router.delete(
    "/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 422, 503),
)
async def delete_draft(draft_id: UUID, drafts: Drafts, account_id: AccountId) -> Response:
    """Immediately delete one caller-owned synchronized draft."""
    await drafts.delete(draft_id, account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
