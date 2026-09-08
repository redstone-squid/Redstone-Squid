"""Stored error report lookup, for whoever is allowed to read internals."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from squid.api.contract import ANONYMOUS, cli_command, contract
from squid.api.dependencies import ErrorReports
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import Page, PageSizeParam, render_page
from squid.api.security import requires
from squid.api.v1.schemas.diagnostics import ErrorReportDetail, ErrorReportSummary
from squid.core.pagination import offset_page
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

# No router-level permission: each route declares its own so `diagnostics.error.read` does not leak onto the
# DELETE route, which needs the more dangerous `diagnostics.error.clear`.
router = APIRouter(prefix="/diagnostics/errors", tags=["diagnostics"])

WorkLostParam = Annotated[
    bool,
    Query(description="Return only failures that permanently abandoned work, such as a dead-lettered job."),
]

ReferenceParam = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_REFERENCE_LENGTH,
        description="The short reference a user was shown, or the full correlation ID from a Request-Id header.",
    ),
]


@router.get(
    "",
    response_model=Page[ErrorReportSummary],
    responses=responses(401, 403, 422),
    dependencies=[Depends(requires(DIAGNOSTICS_ERROR_READ))],
    operation_id="diagnostics_errors_list",
    openapi_extra=contract(
        security=[ANONYMOUS],
        cli=cli_command("errors.list", interaction="direct"),
        scopes=("diagnostics.error.read",),
    ),
)
async def list_error_reports(
    error_reports: ErrorReports,
    page_size: PageSizeParam = 20,
    work_lost: WorkLostParam = False,
) -> Page[ErrorReportSummary]:
    """List the most recent unexpired error reports, newest first; `work_lost` keeps only those that abandoned work."""
    reports = await error_reports.recent(limit=page_size, work_lost_only=work_lost)
    return render_page(offset_page(reports, offset=0, page_size=page_size), ErrorReportSummary.from_domain)


@router.get(
    "/{reference}",
    response_model=ErrorReportDetail,
    responses=responses(
        401,
        403,
        404,
        422,
        describe={404: "No stored report matches the reference, or it has passed its retention window."},
    ),
    dependencies=[Depends(requires(DIAGNOSTICS_ERROR_READ))],
    operation_id="diagnostics_error_get",
    openapi_extra=contract(
        security=[ANONYMOUS],
        cli=cli_command("errors.show", interaction="direct"),
        scopes=("diagnostics.error.read",),
    ),
)
async def get_error_report(reference: ReferenceParam, error_reports: ErrorReports) -> ErrorReportDetail:
    """Resolve a reference, either the short form a Discord error card shows or a full `Request-Id`, to its report."""
    report, matches = await error_reports.lookup(reference)
    return ErrorReportDetail.of(report, matches)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 422),
    dependencies=[Depends(requires(DIAGNOSTICS_ERROR_CLEAR)), Depends(enforce_request_idempotency)],
    operation_id="diagnostics_errors_clear",
    openapi_extra=contract(
        security=[ANONYMOUS],
        cli=cli_command("errors.clear", interaction="direct"),
        scopes=("diagnostics.error.clear",),
    ),
)
async def clear_error_reports(error_reports: ErrorReports) -> Response:
    """Delete every stored error report, expired or not.

    `diagnostics.error.clear` is tagged destructive and excluded from both built-in admin roles, so by default
    only the bot owner holds it.
    """
    await error_reports.clear_all()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
