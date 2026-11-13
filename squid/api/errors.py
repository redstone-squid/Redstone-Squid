"""RFC 9457 problem details and Litestar exception handlers."""

import logging
from collections.abc import Mapping
from http import HTTPStatus

from litestar import Litestar, Request, Response
from litestar.exceptions import HTTPException, ValidationException
from litestar.openapi.datastructures import ResponseSpec
from litestar.types import ExceptionHandlersMap, Scope
from pydantic import BaseModel, ConfigDict, Field

from squid.api.i18n import locale_for_request
from squid.api.request_context import resolve_request_id
from squid.api.revisions import InvalidRevisionError, RevisionRequiredError
from squid.builds.errors import BuildRevisionMismatchError, BuildRevisionRequiredError
from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    ErrorCode,
    JSONValue,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    SquidError,
    ValidationError,
)
from squid.core.i18n import localization_for, tr
from squid.diagnostics.log_capture import captured
from squid.edits.domain import ConcurrentEditError, RevisionConflictError
from squid.observability import correlation_id
from squid_i18n import localization_scope

logger = logging.getLogger(__name__)

PROBLEM_DETAIL_MEDIA_TYPE = "application/problem+json"
INTERNAL_ERROR_DETAIL = tr(t"api-internal-server-error-occurred = An internal server error occurred.")
SERVICE_UNAVAILABLE_DETAIL = tr(
    t"api-service-unavailable = A required service is temporarily unavailable. Please try again later."
)


def _route_template(request: Request) -> str | None:
    """Name the matched route rather than the concrete URL.

    The path template is low cardinality and groups every failure of one endpoint together;
    `str(request.url)` would put an id, and sometimes a query string, into the stored origin.
    """
    path = request.scope.get("path_template")
    return path if isinstance(path, str) else None


async def _capture(request: Request, error: Exception, request_id: str) -> None:
    """Store a failure when the application has an error-report service.

    Exception handlers may run after dependency resolution fails, so look up the
    service on app.state and allow it to be absent. Catch lookup, buffer, and
    storage errors so reporting cannot prevent the HTTP error response.
    """
    from squid.observability import correlated_log_buffer, correlation_reference

    try:
        services = getattr(getattr(request.app.state, "runtime", None), "services", None)
        reports = getattr(services, "error_reports", None)
        if reports is None:
            return
        buffer = correlated_log_buffer()
        await reports.record(
            error,
            correlation_id=request_id,
            reference=correlation_reference(request_id),
            surface="http",
            origin=f"{request.method} {_route_template(request) or request.url.path}",
            context={"method": request.method, "route": _route_template(request)},
            log_tail=buffer.drain(request_id) if buffer is not None else (),
        )
    except Exception:
        logger.exception("Could not capture an HTTP failure [request_id=%s]", request_id)


class ProblemDetail(BaseModel):
    """RFC 9457 problem detail response with application extensions."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="about:blank")
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: ErrorCode | None = None
    resource: str | None = None
    context: dict[str, JSONValue] | None = None


_PINNED_REASON_PHRASES = {
    # Python changed the 422 reason phrase between versions. Pin it so generated
    # OpenAPI does not change with the interpreter used to export it.
    HTTPStatus.UNPROCESSABLE_ENTITY: "Unprocessable Content",
}


def responses(*statuses: int, describe: Mapping[int, str] | None = None) -> dict[int, ResponseSpec]:
    """Declare the shared problem-details response for each failure status."""
    described = describe or {}
    return {
        status: ResponseSpec(
            generate_examples=False,
            data_container=ProblemDetail,
            media_type=PROBLEM_DETAIL_MEDIA_TYPE,
            description=described.get(
                status, _PINNED_REASON_PHRASES.get(HTTPStatus(status), HTTPStatus(status).phrase)
            ),
        )
        for status in statuses
    }


def _problem_response(
    problem: ProblemDetail,
    locale: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    headers = {"Content-Language": locale}
    headers.update(extra_headers or {})
    return Response(
        status_code=problem.status,
        content=problem.model_dump_json(exclude_none=True).encode(),
        media_type=PROBLEM_DETAIL_MEDIA_TYPE,
        headers=headers,
    )


def _status_for_error(error: SquidError) -> int:
    if isinstance(error, (BuildRevisionRequiredError, RevisionRequiredError)):
        return HTTPStatus.PRECONDITION_REQUIRED
    if isinstance(
        error, (BuildRevisionMismatchError, InvalidRevisionError, RevisionConflictError, ConcurrentEditError)
    ):
        return HTTPStatus.PRECONDITION_FAILED
    if isinstance(error, AuthenticationError):
        return HTTPStatus.UNAUTHORIZED
    if isinstance(error, AuthorizationError):
        return HTTPStatus.FORBIDDEN
    if isinstance(error, NotFoundError):
        return HTTPStatus.NOT_FOUND
    if isinstance(error, ConflictError):
        return HTTPStatus.CONFLICT
    if isinstance(error, RateLimitedError):
        return HTTPStatus.TOO_MANY_REQUESTS
    if isinstance(error, ValidationError):
        return HTTPStatus.BAD_REQUEST
    if isinstance(error, ServiceUnavailableError):
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.INTERNAL_SERVER_ERROR


def handle_squid_error(request: Request, exc: Exception) -> Response:
    """Render a structured application exception."""
    if not isinstance(exc, SquidError):
        return handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    status_code = _status_for_error(exc)
    if isinstance(exc, DomainError):
        with localization_scope(localization_for(locale)):
            title = exc.title if isinstance(exc.title, str) else exc.title.resolve()
            detail = exc.public_detail()
        return _problem_response(
            ProblemDetail(
                title=title,
                status=status_code,
                detail=detail,
                instance=str(request.url),
                code=exc.code,
                resource=exc.resource,
                context=exc.public_context or None,
            ),
            locale,
            extra_headers={"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None,
        )

    request_id = correlation_id()
    # Capture before logging, so the stored tail is what the request was doing before it failed
    # rather than an echo of the traceback the report already carries.
    logger.error(
        "Application failure [request_id=%s code=%s context=%r]",
        request_id,
        exc.code,
        exc.context,
        exc_info=exc,
        extra=captured(),
    )
    service_unavailable = isinstance(exc, ServiceUnavailableError)
    with localization_scope(localization_for(locale)):
        title = (
            tr(t"api-errors-service-unavailable = Service unavailable")
            if service_unavailable
            else tr(t"api-errors-internal-server-error = Internal server error")
        ).resolve()
        detail = (SERVICE_UNAVAILABLE_DETAIL if service_unavailable else INTERNAL_ERROR_DETAIL).resolve()
    return _problem_response(
        ProblemDetail(
            title=title,
            status=status_code,
            detail=detail,
            instance=str(request.url),
            code=exc.code if service_unavailable else ErrorCode.INTERNAL_ERROR,
            resource=exc.resource if service_unavailable else None,
        ),
        locale,
        extra_headers={"Request-Id": request_id},
    )


def handle_request_validation_error(request: Request, exc: Exception) -> Response:
    """Render request validation failures without submitted values."""
    locale = locale_for_request(request)
    errors: list[JSONValue] = []
    if isinstance(exc, ValidationException):
        for error in exc.extra or []:
            if not isinstance(error, dict):
                continue
            key = str(error.get("key", ""))
            # Framework messages can embed submitted enum values, including credentials.
            errors.append(
                {
                    "location": [getattr(error.get("source"), "value", error.get("source", "body")), *key.split(".")],
                    "type": str(error.get("type", "validation_error")),
                    "message": "Invalid value",
                }
            )
    else:
        return handle_unexpected_error(request, exc)
    with localization_scope(localization_for(locale)):
        title = tr(t"api-errors-invalid-request = Invalid request").resolve()
        detail = tr(t"api-request-validation-failed = The request did not pass validation.").resolve()
    return _problem_response(
        ProblemDetail(
            title=title,
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=detail,
            instance=str(request.url),
            code=ErrorCode.INVALID_REQUEST,
            context={"errors": errors},
        ),
        locale,
    )


def handle_http_error(request: Request, exc: Exception) -> Response:
    """Render framework-level HTTP errors as problem details."""
    if not isinstance(exc, HTTPException):
        return handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        with localization_scope(localization_for(locale)):
            title = tr(t"api-errors-http-error = HTTP error").resolve()
    return _problem_response(
        ProblemDetail(
            title=title,
            status=exc.status_code,
            detail=str(exc.detail),
            instance=str(request.url),
            code=ErrorCode.NOT_FOUND if exc.status_code == HTTPStatus.NOT_FOUND else ErrorCode.INVALID_REQUEST,
        ),
        locale,
        extra_headers={"Request-Id": resolve_request_id(request.headers)},
    )


def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Log and redact an unexpected exception."""
    locale = locale_for_request(request)
    request_id = correlation_id()
    logger.error("Unhandled HTTP exception [request_id=%s]", request_id, exc_info=exc, extra=captured())
    with localization_scope(localization_for(locale)):
        title = tr(t"api-errors-internal-server-error = Internal server error").resolve()
        detail = INTERNAL_ERROR_DETAIL.resolve()
    return _problem_response(
        ProblemDetail(
            title=title,
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=detail,
            instance=str(request.url),
            code=ErrorCode.INTERNAL_ERROR,
        ),
        locale,
        extra_headers={"Request-Id": request_id},
    )


async def capture_exception(exc: Exception, scope: Scope) -> None:
    """Capture unexpected failures before the synchronous exception renderer runs."""
    if isinstance(exc, (DomainError, HTTPException)):
        return
    from squid.api.idempotency import IdempotencyReplay

    if isinstance(exc, IdempotencyReplay):
        return
    await _capture(Request(scope), exc, correlation_id())


def exception_handlers() -> ExceptionHandlersMap:
    """Return the synchronous renderers used by Litestar's exception middleware."""
    from squid.api.idempotency import IdempotencyReplay, replay_response

    return {
        IdempotencyReplay: replay_response,
        SquidError: handle_squid_error,
        ValidationException: handle_request_validation_error,
        HTTPException: handle_http_error,
        Exception: handle_unexpected_error,
    }


def register_exception_handlers(app: Litestar) -> None:
    app.exception_handlers.update(exception_handlers())
