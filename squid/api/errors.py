"""RFC 9457 problem details and FastAPI exception handlers."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from squid.api.i18n import locale_for_request
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
from squid_ui.text import localization_scope

logger = logging.getLogger(__name__)

PROBLEM_DETAIL_MEDIA_TYPE = "application/problem+json"
INTERNAL_ERROR_DETAIL = tr(t"An internal server error occurred.")
SERVICE_UNAVAILABLE_DETAIL = tr(t"A required service is temporarily unavailable. Please try again later.")


class ExceptionRegistrar(Protocol):
    """Minimal exception-registration surface implemented by FastAPI."""

    def add_exception_handler(self, exc_class_or_status_code: Any, handler: Any) -> None:
        """Route exceptions of that class (or responses with that status) to `handler`."""
        ...


def correlation_id() -> str:
    """The active correlation id; imports `squid.observability` lazily so only failing requests pay for it."""
    from squid.observability import correlation_id as active_correlation_id

    return active_correlation_id()


def _route_template(request: Request) -> str | None:
    """The matched route's path template, not the concrete URL: low cardinality and free of ids and query strings."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


async def _capture(request: Request, error: Exception, request_id: str) -> None:
    """Store the failure when the process has an `ErrorReportService`; a no-op otherwise.

    Reached through `app.state`, not a dependency: exception handlers run after dependency resolution has
    unwound, and test apps have no service graph. Never raises, since a handler that raises turns the
    rendered 500 into a bare ASGI failure with no problem document.
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
    # `HTTPStatus.phrase` for 422 differs between Python 3.12 and 3.13+, and the committed OpenAPI
    # document must generate identically on every interpreter the suite runs on.
    HTTPStatus.UNPROCESSABLE_ENTITY: "Unprocessable Content",
}


def responses(*statuses: int, describe: Mapping[int, str] | None = None) -> dict[int | str, dict[str, Any]]:
    """Declare RFC 9457 problem responses for the given statuses.

    `describe` overrides a status's reason phrase, most often to say that a 404 also covers a resource the
    caller may not see.
    """
    described = describe or {}
    return {
        status: {
            "model": ProblemDetail,
            "content": {PROBLEM_DETAIL_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}},
            "description": described.get(
                status, _PINNED_REASON_PHRASES.get(HTTPStatus(status), HTTPStatus(status).phrase)
            ),
        }
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
        content=problem.model_dump_json(exclude_none=True),
        media_type=PROBLEM_DETAIL_MEDIA_TYPE,
        headers=headers,
    )


def _status_for_error(error: SquidError) -> int:
    if isinstance(error, BuildRevisionRequiredError):
        return HTTPStatus.PRECONDITION_REQUIRED
    if isinstance(error, BuildRevisionMismatchError):
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


async def handle_squid_error(request: Request, exc: Exception) -> Response:
    """Render a `SquidError` as a problem document with the status `_status_for_error` assigns.

    A `DomainError` publishes its code, resource and public context; any other `SquidError` is captured,
    logged, and redacted to a generic 500 or 503 carrying only the Request-Id.
    """
    if not isinstance(exc, SquidError):
        return await handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    status_code = _status_for_error(exc)
    if isinstance(exc, DomainError):
        with localization_scope(localization_for(locale)):
            title = tr(exc.title)
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
    await _capture(request, exc, request_id)
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
        title = tr("Service unavailable") if service_unavailable else tr("Internal server error")
        detail = tr(SERVICE_UNAVAILABLE_DETAIL if service_unavailable else INTERNAL_ERROR_DETAIL)
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


async def handle_request_validation_error(request: Request, exc: Exception) -> Response:
    """Render FastAPI request validation failures without submitted values."""
    errors_method = getattr(exc, "errors", None)
    if not callable(errors_method):
        return await handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    raw_errors = cast(list[dict[str, Any]], errors_method())
    errors: list[JSONValue] = [
        {
            "location": [item if isinstance(item, int) else str(item) for item in error["loc"]],
            "type": str(error["type"]),
            "message": str(error["msg"]),
        }
        for error in raw_errors
    ]
    with localization_scope(localization_for(locale)):
        title = tr("Invalid request")
        detail = tr("The request did not pass validation.")
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


async def handle_http_error(request: Request, exc: Exception) -> Response:
    """Render framework-level HTTP errors as problem details."""
    if not isinstance(exc, StarletteHTTPException):
        return await handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        with localization_scope(localization_for(locale)):
            title = tr("HTTP error")
    return _problem_response(
        ProblemDetail(
            title=title,
            status=exc.status_code,
            detail=str(exc.detail),
            instance=str(request.url),
            code=ErrorCode.NOT_FOUND if exc.status_code == HTTPStatus.NOT_FOUND else ErrorCode.INVALID_REQUEST,
        ),
        locale,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Log and redact an unexpected exception."""
    locale = locale_for_request(request)
    request_id = correlation_id()
    await _capture(request, exc, request_id)
    logger.error("Unhandled HTTP exception [request_id=%s]", request_id, exc_info=exc, extra=captured())
    with localization_scope(localization_for(locale)):
        title = tr("Internal server error")
        detail = tr(INTERNAL_ERROR_DETAIL)
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


def register_exception_handlers(app: ExceptionRegistrar) -> None:
    """Register application-wide FastAPI exception handlers."""
    from fastapi.exceptions import RequestValidationError

    from squid.api.idempotency import IdempotencyReplay, replay_response

    app.add_exception_handler(IdempotencyReplay, replay_response)
    app.add_exception_handler(SquidError, handle_squid_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
