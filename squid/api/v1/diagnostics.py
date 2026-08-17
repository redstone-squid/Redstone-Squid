"""Stored error report lookup, for whoever is allowed to read internals."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from squid.api.dependencies import ErrorReports
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import Page, PageSizeParam, render_page
from squid.api.security import requires
from squid.api.v1.schemas.diagnostics import ErrorReportDetail, ErrorReportSummary
from squid.core.pagination import offset_page
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

# No router-level dependency: clearing every report is a distinct, more dangerous capability
# than reading one, so each route declares the permission it actually needs instead of the GET
# routes' `diagnostics.error.read` leaking onto the DELETE route below.
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
)
async def list_error_reports(
    error_reports: ErrorReports,
    page_size: PageSizeParam = 20,
    work_lost: WorkLostParam = False,
) -> Page[ErrorReportSummary]:
    """List the most recent unexpired error reports, newest first.

    Most reports are failures something recovered from, because capture follows the logs. Set
    `work_lost` to see only the ones that permanently abandoned work.
    """
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
)
async def get_error_report(reference: ReferenceParam, error_reports: ErrorReports) -> ErrorReportDetail:
    """Resolve a quoted reference to the failure behind it.

    Accepts either width: the short form a Discord error card shows, and the full correlation ID
    that appears in logs and in the `Request-Id` response header.
    """
    report, matches = await error_reports.lookup(reference)
    return ErrorReportDetail.of(report, matches)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 422),
    dependencies=[Depends(requires(DIAGNOSTICS_ERROR_CLEAR)), Depends(enforce_request_idempotency)],
)
async def clear_error_reports(error_reports: ErrorReports) -> Response:
    """Delete every stored error report, expired or not.

    Denied by default to everyone but the bot owner: `diagnostics.error.clear` is tagged
    destructive, which both built-in admin roles explicitly exclude.
    """
    await error_reports.clear_all()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
