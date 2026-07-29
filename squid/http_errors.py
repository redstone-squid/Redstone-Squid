"""RFC 9457 problem details and FastAPI exception handlers."""

import logging
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from squid.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    ErrorCode,
    JSONValue,
    NotFoundError,
    ServiceUnavailableError,
    SquidError,
    ValidationError,
)

logger = logging.getLogger(__name__)

PROBLEM_DETAIL_MEDIA_TYPE = "application/problem+json"
INTERNAL_ERROR_DETAIL = "An internal server error occurred."
SERVICE_UNAVAILABLE_DETAIL = "A required service is temporarily unavailable. Please try again later."


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


def _problem_response(problem: ProblemDetail) -> Response:
    headers = {"X-Error-ID": problem.error_id} if problem.error_id is not None else None
    return Response(
        status_code=problem.status,
        content=problem.model_dump_json(exclude_none=True),
        media_type=PROBLEM_DETAIL_MEDIA_TYPE,
        headers=headers,
    )


def _status_for_error(error: SquidError) -> int:
    if isinstance(error, AuthenticationError):
        return HTTPStatus.UNAUTHORIZED
    if isinstance(error, AuthorizationError):
        return HTTPStatus.FORBIDDEN
    if isinstance(error, NotFoundError):
        return HTTPStatus.NOT_FOUND
    if isinstance(error, ConflictError):
        return HTTPStatus.CONFLICT
    if isinstance(error, ValidationError):
        return HTTPStatus.BAD_REQUEST
    if isinstance(error, ServiceUnavailableError):
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.INTERNAL_SERVER_ERROR


def _new_error_id() -> str:
    return uuid4().hex[:12]


async def handle_squid_error(request: Request, exc: Exception) -> Response:
    """Render a structured application exception."""
    if not isinstance(exc, SquidError):
        return await handle_unexpected_error(request, exc)

    status_code = _status_for_error(exc)
    if isinstance(exc, DomainError):
        return _problem_response(
            ProblemDetail(
                title=exc.title,
                status=status_code,
                detail=exc.public_detail(),
                instance=str(request.url),
                code=exc.code,
                resource=exc.resource,
                context=exc.public_context or None,
            )
        )

    error_id = _new_error_id()
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
            title="Service unavailable" if service_unavailable else "Internal server error",
            status=status_code,
            detail=SERVICE_UNAVAILABLE_DETAIL if service_unavailable else INTERNAL_ERROR_DETAIL,
            instance=str(request.url),
            code=exc.code if service_unavailable else ErrorCode.INTERNAL_ERROR,
            resource=exc.resource if service_unavailable else None,
            error_id=error_id,
        )
    )


async def handle_request_validation_error(request: Request, exc: Exception) -> Response:
    """Render FastAPI request validation failures without submitted values."""
    if not isinstance(exc, RequestValidationError):
        return await handle_unexpected_error(request, exc)

    errors: list[JSONValue] = [
        {
            "location": [item if isinstance(item, int) else str(item) for item in error["loc"]],
            "type": str(error["type"]),
            "message": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return _problem_response(
        ProblemDetail(
            title="Invalid request",
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="The request did not pass validation.",
            instance=str(request.url),
            code=ErrorCode.INVALID_REQUEST,
            context={"errors": errors},
        )
    )


async def handle_http_error(request: Request, exc: Exception) -> Response:
    """Render framework-level HTTP errors as problem details."""
    if not isinstance(exc, StarletteHTTPException):
        return await handle_unexpected_error(request, exc)

    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP error"
    return _problem_response(
        ProblemDetail(
            title=title,
            status=exc.status_code,
            detail=str(exc.detail),
            instance=str(request.url),
            code=ErrorCode.NOT_FOUND if exc.status_code == HTTPStatus.NOT_FOUND else ErrorCode.INVALID_REQUEST,
        )
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Log and redact an unexpected exception."""
    error_id = _new_error_id()
    logger.error("Unhandled HTTP exception [error_id=%s]", error_id, exc_info=exc)
    return _problem_response(
        ProblemDetail(
            title="Internal server error",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=INTERNAL_ERROR_DETAIL,
            instance=str(request.url),
            code=ErrorCode.INTERNAL_ERROR,
            error_id=error_id,
        )
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register application-wide FastAPI exception handlers."""
    app.add_exception_handler(SquidError, handle_squid_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
