"""Publish route metadata and shared definitions in the OpenAPI document.

Routes declare their security requirements and CLI metadata through `contract()`.
This module adds shared security schemes, response headers, and each route's
explicit security declaration.
"""

from copy import deepcopy
from typing import Any, override

from litestar import Litestar, Request
from litestar._openapi.schema_generation.schema import SchemaCreator
from litestar.openapi.plugins import JsonRenderPlugin, SwaggerRenderPlugin
from litestar.openapi.spec import OpenAPIType, Schema
from litestar.plugins import OpenAPISchemaPlugin
from litestar.typing import FieldDefinition
from pydantic_core import to_jsonable_python

from squid.api.contract import SECURITY_PLACEHOLDER_KEY, iter_contract_routes, validate_contract
from squid.api.errors import ProblemDetail, responses
from squid.core.errors import JSONValue
from squid.submissions.domain import (
    DRAFT_CHANGE_KEY_MAX_LENGTH,
    DRAFT_CHANGE_KEY_MIN_LENGTH,
    DRAFT_CHANGE_KEY_PATTERN,
    DraftChangeKey,
)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def install_openapi_contract(app: Litestar) -> None:
    """Validate route declarations before publishing the HTTP contract."""
    validate_contract(app)
    for route, handler in iter_contract_routes(app):
        if route.path.startswith("/v1/"):
            handler.responses = {**responses(429), **(handler.responses or {})}


def openapi_document(app: Litestar) -> dict[str, Any]:
    """Render Litestar's schema with the application-owned security and CLI contract."""
    return _contract_document(app, app.openapi_schema.to_schema())


def _contract_document(app: Litestar, schema: dict[str, Any]) -> dict[str, Any]:
    document = to_jsonable_python(schema)
    operations = {
        operation["operationId"]: operation
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method in _HTTP_METHODS
    }
    for _route, handler in iter_contract_routes(app):
        operation = operations.get(handler.operation_id)
        if operation is None:
            continue
        extra = deepcopy(handler.opt)
        for status, response in extra.pop("responses", {}).items():
            operation.setdefault("responses", {}).setdefault(str(status), {}).update(response)
        operation.update(extra)
        hook = handler.resolve_before_request()
        hook_name = getattr(getattr(hook, "func", hook), "__name__", "")
        if hook_name in {"reserve_idempotent_before_request", "reserve_edit_before_request"}:
            operation.setdefault("parameters", []).append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": False,
                    "schema": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "maxLength": 255,
                        "pattern": r"^[\x21-\x7e]+$",
                    },
                    "description": "Deduplicate an equivalent mutation in its server-derived caller namespace for 24 hours.",
                }
            )
    _postprocess(document)
    return document


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
    _install_validation_errors(document)
    _rename_security_placeholder(document)
    _install_request_id_headers(document)


def _rename_security_placeholder(document: dict[str, Any]) -> None:
    """Publish route security metadata under the standard OpenAPI key."""
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


def _install_validation_errors(document: dict[str, Any]) -> None:
    """Describe the 422 problem renderer instead of Litestar's default 400 body."""
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            responses = operation["responses"]
            default = responses.get("400", {})
            schema = default.get("content", {}).get("application/json", {}).get("schema", {})
            if schema.get("description") == "Validation Exception":
                responses.pop("400")
            elif not operation.get("parameters") and not operation.get("requestBody"):
                continue
            responses.setdefault(
                "422",
                {
                    "description": "Unprocessable Entity",
                    "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}},
                },
            )
            components = document.setdefault("components", {}).setdefault("schemas", {})
            if "ProblemDetail" not in components:
                problem = ProblemDetail.model_json_schema(ref_template="#/components/schemas/{model}")
                components.update(problem.pop("$defs", {}))
                components["ProblemDetail"] = problem


class ApplicationValueSchemaPlugin(OpenAPISchemaPlugin):
    """Describe recursive JSON and validated domain strings without expanding aliases."""

    @override
    def is_plugin_supported_field(self, field_definition: FieldDefinition) -> bool:
        return field_definition.annotation == JSONValue.__value__ or field_definition.annotation is DraftChangeKey

    @override
    def to_openapi_schema(self, field_definition: FieldDefinition, schema_creator: SchemaCreator) -> Schema:
        if field_definition.annotation is DraftChangeKey:
            return Schema(
                type=OpenAPIType.STRING,
                min_length=DRAFT_CHANGE_KEY_MIN_LENGTH,
                max_length=DRAFT_CHANGE_KEY_MAX_LENGTH,
                pattern=DRAFT_CHANGE_KEY_PATTERN,
            )
        alias = FieldDefinition.from_annotation(JSONValue)
        registry = schema_creator.schema_registry
        schema = registry.get_schema_for_field_definition(alias)
        reference = registry.get_reference_for_field_definition(alias)
        assert reference is not None
        schema.any_of = [
            Schema(type=OpenAPIType.NULL),
            Schema(type=OpenAPIType.BOOLEAN),
            Schema(type=OpenAPIType.NUMBER),
            Schema(type=OpenAPIType.STRING),
            Schema(type=OpenAPIType.ARRAY, items=reference),
            Schema(type=OpenAPIType.OBJECT, additional_properties=reference),
        ]
        return Schema(all_of=[reference])


class ContractJsonRenderPlugin(JsonRenderPlugin):
    """Serve the same security, CLI, and response contract as the exported document."""

    @override
    def render(self, request: Request, openapi_schema: dict[str, Any]) -> bytes:
        return super().render(request, _contract_document(request.app, openapi_schema))


class ContractSwaggerRenderPlugin(SwaggerRenderPlugin):
    """Display the application contract in Swagger's embedded schema."""

    @override
    def render(self, request: Request, openapi_schema: dict[str, Any]) -> bytes:
        return super().render(request, _contract_document(request.app, openapi_schema))
