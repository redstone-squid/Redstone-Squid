"""Stored error report lookup, for whoever is allowed to read internals."""

from typing import Annotated

from litestar import Response, Router, delete, get
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import PathParameter, QueryParameter, SkipValidation

from squid.api.contract import ANONYMOUS, cli_command, contract
from squid.api.dependencies import ErrorReports
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.pagination import Page, PageSizeParam
from squid.api.security import Caller, requires
from squid.api.v1.schemas.diagnostics import ErrorReportDetail, ErrorReportSummary
from squid.core.pagination import offset_page
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

# Reading and clearing reports require different permissions, declared per route.

WorkLostParam = Annotated[
    bool,
    QueryParameter(description="Return only failures that permanently abandoned work, such as a dead-lettered job."),
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
    opt=contract(
        security=[ANONYMOUS], cli=cli_command("errors.list", interaction="direct"), scopes=("diagnostics.error.read",)
    ),
    status_code=200,
    dependencies={"_permission": Provide(requires(DIAGNOSTICS_ERROR_READ))},
)
async def list_error_reports(
    error_reports: ErrorReports,
    page_size: PageSizeParam = 20,
    work_lost: WorkLostParam = False,
    *,
    _permission: NamedDependency[SkipValidation[Caller]],
) -> Page[ErrorReportSummary]:
    """List the most recent unexpired error reports, newest first.

    Reports include failures that were caught and logged. Set work_lost to include
    only failures that permanently abandoned work, such as a dead-lettered job.
    """
    reports = await error_reports.recent(limit=page_size, work_lost_only=work_lost)
    return Page[ErrorReportSummary].from_domain(
        offset_page(reports, offset=0, page_size=page_size), ErrorReportSummary.from_domain
    )


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
    opt=contract(
        security=[ANONYMOUS], cli=cli_command("errors.show", interaction="direct"), scopes=("diagnostics.error.read",)
    ),
    status_code=200,
    dependencies={"_permission": Provide(requires(DIAGNOSTICS_ERROR_READ))},
)
async def get_error_report(
    reference: ReferenceParam, error_reports: ErrorReports, *, _permission: NamedDependency[SkipValidation[Caller]]
) -> ErrorReportDetail:
    """Find a stored failure by its short reference or full correlation ID.

    Short references appear in Discord error cards. Full IDs appear in logs and
    the Request-Id response header.
    """
    report, matches = await error_reports.lookup(reference)
    return ErrorReportDetail.of(report, matches)


@delete(
    "/",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 422),
    operation_id="diagnostics_errors_clear",
    opt=contract(
        security=[ANONYMOUS], cli=cli_command("errors.clear", interaction="direct"), scopes=("diagnostics.error.clear",)
    ),
    dependencies={
        "_permission": Provide(requires(DIAGNOSTICS_ERROR_CLEAR)),
        "_reservation": Provide(reserve_idempotent_request),
    },
)
async def clear_error_reports(
    error_reports: ErrorReports,
    *,
    _permission: NamedDependency[SkipValidation[Caller]],
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Delete every stored error report, expired or not.

    Denied by default to everyone but the bot owner: `diagnostics.error.clear` is tagged
    destructive, which both built-in admin roles explicitly exclude.
    """
    await error_reports.clear_all()
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


router = Router(
    path="/diagnostics/errors",
    tags=["diagnostics"],
    route_handlers=[list_error_reports, get_error_report, clear_error_reports],
)
