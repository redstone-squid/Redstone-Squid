"""Per-route declarations for the public OpenAPI and CLI capability contract.

Each route declares native security requirements and uses `contract()` for
permission scopes, CLI behavior, and response links. Guards and application
services enforce access; these declarations describe the public API. Application
construction checks the declarations before publishing OpenAPI.
"""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, Literal

from litestar import Litestar
from litestar.handlers import HTTPRouteHandler
from litestar.routes import HTTPRoute

from squid.api.capabilities import API_FEATURES

type SecurityRequirement = dict[str, list[str]]
type CliClassification = Literal["command", "browser-only", "transport-only", "internal", "compatibility-alias"]

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
    """Declare a CLI-addressable command's `x-squid-cli` metadata."""
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
    """Classify a browser workflow that has no standalone CLI command."""
    return {"classification": "browser-only"}


def transport_only() -> dict[str, Any]:
    """Classify a supporting route that has no standalone CLI command."""
    return {"classification": "transport-only"}


def internal(rationale: str) -> dict[str, Any]:
    """Classify an operational route and explain its exclusion from CLI commands."""
    return {"classification": "internal", "rationale": rationale}


def compatibility_alias(canonical_operation_id: str) -> dict[str, Any]:
    """Declare a route kept only for backward compatibility with another operation."""
    return {"classification": "compatibility-alias", "canonical_operation_id": canonical_operation_id}


def contract(
    *,
    cli: dict[str, Any],
    scopes: Sequence[str] = (),
    links: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build CLI, permission, and response-link metadata for a route's `opt`."""
    extra: dict[str, Any] = {
        "x-squid-cli": cli,
    }
    if scopes:
        extra["x-required-api-scopes"] = list(scopes)
    if links:
        extra["responses"] = {status_code: {"links": dict(link_map)} for status_code, link_map in links.items()}
    return extra


def validate_contract(app: Litestar) -> None:
    """Raise when route-declared contract metadata is missing or internally inconsistent.

    Runs on every app construction, so an unclassified route breaks the app rather than
    silently missing from the published contract.
    """
    routes = list(iter_contract_routes(app))

    operation_ids: list[str] = []
    commands: list[str] = []
    aliases: dict[str, str] = {}
    for route, handler in routes:
        methods = set(handler.http_methods)
        operation_id = handler.operation_id
        if not isinstance(operation_id, str) or not operation_id:
            msg = f"Route {route.path!r} {sorted(methods)} has no explicit operation_id."
            raise ValueError(msg)
        operation_ids.append(operation_id)

        extra = handler.opt
        if not handler.resolve_security() or "x-squid-cli" not in extra:
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
            for requirement in handler.resolve_security():
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

    for _route, handler in routes:
        extra = handler.opt
        for response in extra.get("responses", {}).values():
            for link in response.get("links", {}).values():
                target = link.get("operationId")
                if target not in identifiers:
                    msg = f"Unknown OpenAPI response link target: {target!r}."
                    raise ValueError(msg)


def iter_contract_routes(app: Litestar) -> Iterator[tuple[HTTPRoute, HTTPRouteHandler]]:
    """Visit declared HTTP handlers once per route, excluding generated framework endpoints."""
    for route in app.routes:
        if not isinstance(route, HTTPRoute):
            continue
        seen: set[int] = set()
        for handler, _ in route.route_handler_map.values():
            if id(handler) in seen or not handler.resolve_include_in_schema():
                continue
            seen.add(id(handler))
            yield route, handler
