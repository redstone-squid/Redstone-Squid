"""Stored error report lookup, for whoever is allowed to read internals."""

from datetime import UTC, datetime
from typing import Annotated

from litestar import Response, Router, delete, get
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import FromQuery, PathParameter, QueryParameter, SkipValidation
from whenever import Instant

from squid.api.contract import ANONYMOUS, cli_command, contract, transport_only
from squid.api.dependencies import CurrentCaller, ErrorReports, get_error_reports
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.pagination import (
    CursorParam,
    OffsetParam,
    Page,
    PageSizeParam,
    resolve_selector,
)
from squid.api.security import requires
from squid.api.v1.schemas.diagnostics import ErrorReportDetail, ErrorReportStats, ErrorReportSummary
from squid.core.diagnostics.domain import MAX_REFERENCE_LENGTH, ErrorReportFilters
from squid.core.errors import ErrorCode
from squid.core.pagination import ChronologicalSort
from squid.core.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

# Reading and clearing reports require different permissions, declared per route.

WorkLostParam = Annotated[
    bool,
    QueryParameter(description="Return only failures that permanently abandoned work, such as a dead-lettered job."),
]

SurfaceParam = Annotated[
    str | None,
    QueryParameter(
        min_length=1,
        description="Only failures from this surface, such as `application_command` or `route`.",
    ),
]
ErrorCodeParam = Annotated[
    ErrorCode | None,
    QueryParameter(description="Only failures that carried this application error code."),
]
OccurredAfterParam = Annotated[
    datetime | None,
    QueryParameter(description="Only failures at or after this time. A time without an offset is UTC."),
]
OccurredBeforeParam = Annotated[
    datetime | None,
    QueryParameter(description="Only failures before this time. A time without an offset is UTC."),
]

ReferenceParam = Annotated[
    str,
    PathParameter(
        min_length=1,
        max_length=MAX_REFERENCE_LENGTH,
        description="The short reference a user was shown, or the full correlation ID from a Request-Id header.",
    ),
]


@get(
    "/",
    responses=responses(401, 403, 422),
    operation_id="diagnostics_errors_list",
    security=[ANONYMOUS],
    opt=contract(cli=cli_command("errors.list", interaction="direct"), scopes=("diagnostics.error.read",)),
    status_code=200,
    guards=[requires(DIAGNOSTICS_ERROR_READ)],
)
async def list_error_reports(
    caller: CurrentCaller,
    error_reports: ErrorReports,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.NEWEST,
    cursor: CursorParam = None,
    work_lost: WorkLostParam = False,
    surface: SurfaceParam = None,
    error_code: ErrorCodeParam = None,
    occurred_after: OccurredAfterParam = None,
    occurred_before: OccurredBeforeParam = None,
) -> Page[ErrorReportSummary]:
    """List unexpired error reports that match every filter, newest first.

    Reports include failures that were caught and logged. Anchors seek on the
    report's occurrence time and ID, so a reader keeps their place while new
    failures arrive.
    """
    filters = ErrorReportFilters(
        surface=surface,
        error_code=error_code,
        occurred_after=None if occurred_after is None else _instant(occurred_after),
        occurred_before=None if occurred_before is None else _instant(occurred_before),
        work_lost_only=work_lost,
    )
    page = await error_reports.recent(
        context=caller.context(),
        selector=resolve_selector(offset=offset, cursor=cursor, sort=sort),
        page_size=page_size,
        filters=filters,
    )
    return Page[ErrorReportSummary].from_domain(page, ErrorReportSummary.from_domain)


def _instant(value: datetime) -> Instant:
    return Instant(value if value.tzinfo is not None else value.replace(tzinfo=UTC))


@get(
    "/stats",
    responses=responses(401, 403, 422),
    operation_id="diagnostics_errors_stats",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only(), scopes=("diagnostics.error.read",)),
    status_code=200,
    guards=[requires(DIAGNOSTICS_ERROR_READ)],
)
async def error_report_stats(caller: CurrentCaller, error_reports: ErrorReports) -> ErrorReportStats:
    """Count unexpired reports per UTC day and surface, for charting failures over the retention window."""
    counts = await error_reports.daily_counts(context=caller.context())
    return ErrorReportStats.of(counts, retention_hours=error_reports.retention_hours)


@get(
    "/{reference:str}",
    responses=responses(
        401,
        403,
        404,
        422,
        describe={404: "No stored report matches the reference, or it has passed its retention window."},
    ),
    operation_id="diagnostics_error_get",
    security=[ANONYMOUS],
    opt=contract(cli=cli_command("errors.show", interaction="direct"), scopes=("diagnostics.error.read",)),
    status_code=200,
    guards=[requires(DIAGNOSTICS_ERROR_READ)],
)
async def get_error_report(
    reference: ReferenceParam, error_reports: ErrorReports, caller: CurrentCaller
) -> ErrorReportDetail:
    """Find a stored failure by its short reference or full correlation ID.

    Short references appear in Discord error cards. Full IDs appear in logs and
    the Request-Id response header.
    """
    report, matches = await error_reports.lookup(reference, context=caller.context())
    return ErrorReportDetail.of(report, matches)


@delete(
    "/",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 422),
    operation_id="diagnostics_errors_clear",
    security=[ANONYMOUS],
    opt=contract(cli=cli_command("errors.clear", interaction="direct"), scopes=("diagnostics.error.clear",)),
    guards=[requires(DIAGNOSTICS_ERROR_CLEAR)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def clear_error_reports(
    caller: CurrentCaller,
    error_reports: ErrorReports,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Delete every stored error report, expired or not.

    Denied by default to everyone but the bot owner: `diagnostics.error.clear` is tagged
    destructive, which both built-in admin roles explicitly exclude.
    """
    await error_reports.clear_all(context=caller.context())
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


router = Router(
    dependencies={"error_reports": Provide(get_error_reports, sync_to_thread=False)},
    path="/diagnostics/errors",
    tags=["diagnostics"],
    route_handlers=[list_error_reports, error_report_stats, get_error_report, clear_error_reports],
)
