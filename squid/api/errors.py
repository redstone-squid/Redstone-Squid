"""RFC 9457 problem details and FastAPI exception handlers."""

import logging
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
from squid.core.i18n import _, translate

logger = logging.getLogger(__name__)

PROBLEM_DETAIL_MEDIA_TYPE = "application/problem+json"
INTERNAL_ERROR_DETAIL = _("An internal server error occurred.")
SERVICE_UNAVAILABLE_DETAIL = _("A required service is temporarily unavailable. Please try again later.")


class ExceptionRegistrar(Protocol):
    """Minimal exception-registration surface implemented by FastAPI."""

    def add_exception_handler(self, exc_class_or_status_code: Any, handler: Any) -> None: ...


def correlation_id() -> str:
    """Load tracing support only when an error actually needs correlation."""
    from squid.observability import correlation_id as active_correlation_id

    return active_correlation_id()


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
    error_id: str | None = None


def responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Declare RFC 9457 responses for a route without duplicating OpenAPI metadata."""
    return {
        status: {
            "model": ProblemDetail,
            "content": {PROBLEM_DETAIL_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}},
            "description": HTTPStatus(status).phrase,
        }
        for status in statuses
    }


def _problem_response(problem: ProblemDetail, locale: str) -> Response:
    headers = {"Content-Language": locale}
    if problem.error_id is not None:
        headers["X-Error-ID"] = problem.error_id
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
    """Render a structured application exception."""
    if not isinstance(exc, SquidError):
        return await handle_unexpected_error(request, exc)

    locale = locale_for_request(request)
    status_code = _status_for_error(exc)
    if isinstance(exc, DomainError):
        return _problem_response(
            ProblemDetail(
                title=exc.localized_title(locale),
                status=status_code,
                detail=exc.localized_public_detail(locale),
                instance=str(request.url),
                code=exc.code,
                resource=exc.resource,
                context=exc.public_context or None,
            ),
            locale,
        )

    error_id = correlation_id()
    logger.error(
        "Application failure [error_id=%s code=%s context=%r]",
        error_id,
        exc.code,
        exc.context,
        exc_info=exc,
    )
    service_unavailable = isinstance(exc, ServiceUnavailableError)
    return _problem_response(
        ProblemDetail(
            title=translate(locale, _("Service unavailable") if service_unavailable else _("Internal server error")),
            status=status_code,
            detail=translate(locale, SERVICE_UNAVAILABLE_DETAIL if service_unavailable else INTERNAL_ERROR_DETAIL),
            instance=str(request.url),
            code=exc.code if service_unavailable else ErrorCode.INTERNAL_ERROR,
            resource=exc.resource if service_unavailable else None,
            error_id=error_id,
        ),
        locale,
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
    return _problem_response(
        ProblemDetail(
            title=translate(locale, _("Invalid request")),
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=translate(locale, _("The request did not pass validation.")),
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
        title = translate(locale, _("HTTP error"))
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
    error_id = correlation_id()
    logger.error("Unhandled HTTP exception [error_id=%s]", error_id, exc_info=exc)
    return _problem_response(
        ProblemDetail(
            title=translate(locale, _("Internal server error")),
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=translate(locale, INTERNAL_ERROR_DETAIL),
            instance=str(request.url),
            code=ErrorCode.INTERNAL_ERROR,
            error_id=error_id,
        ),
        locale,
    )


def register_exception_handlers(app: ExceptionRegistrar) -> None:
    """Register application-wide FastAPI exception handlers."""
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(SquidError, handle_squid_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
