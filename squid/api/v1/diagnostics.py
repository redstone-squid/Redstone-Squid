"""Stored error report lookup, for whoever is allowed to read internals."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from squid.api.dependencies import ErrorReports
from squid.api.errors import responses
from squid.api.pagination import Page, PageSizeParam, render_page
from squid.api.security import requires
from squid.api.v1.schemas.diagnostics import ErrorReportDetail, ErrorReportSummary
from squid.core.pagination import offset_page
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_READ

router = APIRouter(
    prefix="/diagnostics/errors",
    tags=["diagnostics"],
    dependencies=[Depends(requires(DIAGNOSTICS_ERROR_READ))],
)

ReferenceParam = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_REFERENCE_LENGTH,
        description="The short reference a user was shown, or the full correlation ID from a Request-Id header.",
    ),
]


@router.get("", response_model=Page[ErrorReportSummary], responses=responses(401, 403, 422))
async def list_error_reports(
    error_reports: ErrorReports,
    page_size: PageSizeParam = 20,
) -> Page[ErrorReportSummary]:
    """List the most recent unexpired error reports, newest first."""
    reports = await error_reports.recent(limit=page_size)
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
)
async def get_error_report(reference: ReferenceParam, error_reports: ErrorReports) -> ErrorReportDetail:
    """Resolve a quoted reference to the failure behind it.

    Accepts either width: the short form a Discord error card shows, and the full correlation ID
    that appears in logs and in the `Request-Id` response header.
    """
    report, matches = await error_reports.lookup(reference)
    return ErrorReportDetail.of(report, matches)
