"""Per-route declarations for the public OpenAPI and CLI capability contract.

Each route declares its security, CLI classification, required scopes, and response links through
`contract()`; `validate_contract` fails app construction when a route ships without them or they are
internally inconsistent.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

from fastapi import FastAPI
from fastapi.routing import APIRoute

from squid.api.capabilities import API_FEATURES

type SecurityRequirement = dict[str, list[str]]
type CliClassification = Literal["command", "browser-only", "transport-only", "internal", "compatibility-alias"]

SECURITY_PLACEHOLDER_KEY: Final = "x-squid-security"
"""Merged onto the operation by `openapi_extra`, then renamed to `security` in `squid/api/openapi.py`.

Not written as `security` directly: operations depending on `current_caller` already carry a
FastAPI-generated `security: [{"ApiCredential": []}]`, and `openapi_extra`'s deep merge concatenates
lists, so the declared requirements would be appended to that entry instead of replacing it.
"""

_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

ANONYMOUS: Final[SecurityRequirement] = {}
SERVICE: Final[SecurityRequirement] = {"ApiCredential": []}
WEB: Final[SecurityRequirement] = {"WebSession": []}
WEB_WRITE: Final[SecurityRequirement] = {"WebSession": [], "CsrfToken": []}
DEVICE: Final[SecurityRequirement] = {"DeviceSession": []}
MINECRAFT: Final[SecurityRequirement] = {"MinecraftPlayer": []}
PAPER: Final[SecurityRequirement] = {"PaperInstallationId": [], "PaperInstallationSecret": []}


def cli_command(
    command: str,
    *,
    features: Sequence[str] = (),
    interaction: Literal["direct", "browser-continuation"],
) -> dict[str, Any]:
    """Declare a CLI-addressable command's `x-squid-cli` metadata.

    Raises `ValueError` when `features` names an identifier outside `API_FEATURES`.
    """
    if not set(features) <= API_FEATURES:
        msg = f"Unknown API feature on CLI command {command!r}."
        raise ValueError(msg)
    return {
        "classification": "command",
        "command": command,
        "required_api_features": list(features),
        "interaction": interaction,
    }


def browser_only() -> dict[str, Any]:
    """Declare a route reachable only from a signed-in browser session."""
    return {"classification": "browser-only"}


def transport_only() -> dict[str, Any]:
    """Declare a route with no CLI-facing classification of its own."""
    return {"classification": "transport-only"}


def internal(rationale: str) -> dict[str, Any]:
    """Declare an operational route that is not part of the public contract."""
    return {"classification": "internal", "rationale": rationale}


def compatibility_alias(canonical_operation_id: str) -> dict[str, Any]:
    """Declare a route kept only for backward compatibility with another operation."""
    return {"classification": "compatibility-alias", "canonical_operation_id": canonical_operation_id}


def contract(
    *,
    security: Sequence[SecurityRequirement],
    cli: dict[str, Any],
    scopes: Sequence[str] = (),
    links: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the `openapi_extra` carrying one route's permanent contract metadata."""
    extra: dict[str, Any] = {
        SECURITY_PLACEHOLDER_KEY: [dict(requirement) for requirement in security],
        "x-squid-cli": cli,
    }
    if scopes:
        extra["x-required-api-scopes"] = list(scopes)
    if links:
        extra["responses"] = {status_code: {"links": dict(link_map)} for status_code, link_map in links.items()}
    return extra


def validate_contract(app: FastAPI) -> None:
    """Raise `ValueError` when route-declared contract metadata is missing or internally inconsistent.

    Runs on every app construction, so an unclassified route breaks startup instead of silently
    missing from the published contract.
    """
    routes = [route for route in app.routes if isinstance(route, APIRoute) and route.include_in_schema]

    operation_ids: list[str] = []
    commands: list[str] = []
    aliases: dict[str, str] = {}
    for route in routes:
        methods = route.methods or frozenset()
        operation_id = route.operation_id
        if not operation_id:
            msg = f"Route {route.path!r} {sorted(methods)} has no explicit operation_id."
            raise ValueError(msg)
        operation_ids.append(operation_id)

        extra = route.openapi_extra or {}
        if SECURITY_PLACEHOLDER_KEY not in extra or "x-squid-cli" not in extra:
            msg = f"Operation {operation_id!r} has no declared contract() metadata."
            raise ValueError(msg)

        cli = extra["x-squid-cli"]
        classification = cli.get("classification")
        if classification == "command":
            commands.append(cli["command"])
            if cli.get("interaction") not in {"direct", "browser-continuation"}:
                msg = f"Command operation {operation_id!r} has an invalid interaction."
                raise ValueError(msg)
            if not set(cli.get("required_api_features", ())) <= API_FEATURES:
                msg = f"Unknown API feature on command operation {operation_id!r}."
                raise ValueError(msg)
        elif classification == "compatibility-alias":
            aliases[operation_id] = cli["canonical_operation_id"]

        if methods & _UNSAFE_METHODS:
            for requirement in extra[SECURITY_PLACEHOLDER_KEY]:
                if "WebSession" in requirement and "CsrfToken" not in requirement:
                    msg = f"Operation {operation_id!r} accepts WebSession on an unsafe method without CsrfToken."
                    raise ValueError(msg)

    if len(operation_ids) != len(set(operation_ids)):
        msg = "OpenAPI operation ids must be unique."
        raise ValueError(msg)
    if len(commands) != len(set(commands)):
        msg = "CLI commands must be unique."
        raise ValueError(msg)

    identifiers = set(operation_ids)
    for alias, target in aliases.items():
        if target not in identifiers:
            msg = f"Unknown canonical operation for compatibility alias {alias!r}."
            raise ValueError(msg)
        if target in aliases:
            msg = f"Chained compatibility alias at {alias!r}."
            raise ValueError(msg)

    for route in routes:
        extra = route.openapi_extra or {}
        for response in extra.get("responses", {}).values():
            for link in response.get("links", {}).values():
                target = link.get("operationId")
                if target not in identifiers:
                    msg = f"Unknown OpenAPI response link target: {target!r}."
                    raise ValueError(msg)
