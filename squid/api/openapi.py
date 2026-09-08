"""Document-level OpenAPI postprocessing: security schemes, shared headers, and two document-wide passes.

Per-operation metadata lives on each route via `squid.api.contract.contract()`.
"""

from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from squid.api.contract import SECURITY_PLACEHOLDER_KEY, validate_contract

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def install_openapi_contract(app: FastAPI) -> None:
    """Replace `app.openapi` with a cached generator that runs `_postprocess` once.

    Raises `ValueError` through `validate_contract` when any route lacks contract metadata.
    """
    validate_contract(app)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            document = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            _postprocess(document)
            app.openapi_schema = document
        return cast(dict[str, Any], app.openapi_schema)

    app.openapi = custom_openapi


def _postprocess(document: dict[str, Any]) -> None:
    components = document.setdefault("components", {})
    components["securitySchemes"] = {
        "ApiCredential": {"type": "apiKey", "in": "header", "name": "Authorization"},
        "WebSession": {"type": "apiKey", "in": "cookie", "name": "__Host-squid_session"},
        "CsrfToken": {"type": "apiKey", "in": "header", "name": "CSRF-Token"},
        "DeviceSession": {"type": "http", "scheme": "bearer", "bearerFormat": "Squid CLI session"},
        "MinecraftPlayer": {"type": "http", "scheme": "bearer", "bearerFormat": "Squid player grant"},
        "PaperInstallationId": {"type": "apiKey", "in": "header", "name": "Squid-Installation-ID"},
        "PaperInstallationSecret": {"type": "apiKey", "in": "header", "name": "Squid-Installation-Secret"},
    }
    components["headers"] = {
        "RequestId": {
            "description": "Correlation id for this request; an echo of a valid inbound Request-Id when supplied.",
            "schema": {"type": "string", "pattern": "^[A-Za-z0-9._-]{8,128}$"},
        }
    }
    _rename_security_placeholder(document)
    _install_request_id_headers(document)


def _rename_security_placeholder(document: dict[str, Any]) -> None:
    """Rename each route's `x-squid-security` placeholder to `security`.

    Rebuilt as a dict comprehension rather than pop-and-reinsert so an operation that already carries a
    FastAPI-generated `security` (from a `Security(...)` dependency) keeps that key's position with the
    placeholder's value.
    """
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or SECURITY_PLACEHOLDER_KEY not in operation:
                continue
            path_item[method] = {
                ("security" if key == SECURITY_PLACEHOLDER_KEY else key): value for key, value in operation.items()
            }


def _install_request_id_headers(document: dict[str, Any]) -> None:
    """Declare the Request-Id correlation header the middleware emits on every response."""
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            for response in operation["responses"].values():
                response.setdefault("headers", {})["Request-Id"] = {"$ref": "#/components/headers/RequestId"}
