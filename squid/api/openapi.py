"""Deterministic postprocessing for the public OpenAPI and CLI capability contract."""

from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from squid.api.capabilities import API_FEATURES

type CliClassification = Literal["command", "browser-only", "transport-only", "internal", "compatibility-alias"]
type SecurityRequirement = dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class OperationContract:
    """Permanent metadata for one HTTP operation."""

    path: str
    method: Literal["get", "post", "put", "patch", "delete"]
    operation_id: str
    classification: CliClassification = "transport-only"
    command: str | None = None
    features: tuple[str, ...] = ()
    interaction: Literal["direct", "browser-continuation"] | None = None
    canonical_operation_id: str | None = None
    rationale: str | None = None


def _operation(
    method: Literal["get", "post", "put", "patch", "delete"],
    path: str,
    operation_id: str,
    classification: CliClassification = "transport-only",
    **metadata: Any,
) -> OperationContract:
    return OperationContract(path, method, operation_id, classification, **metadata)


OPERATIONS = (
    _operation("get", "/livez", "health_live", "internal", rationale="Process liveness probe."),
    _operation("get", "/readyz", "health_ready", "internal", rationale="Deployment readiness probe."),
    _operation(
        "get", "/health", "health_ready_compatibility", "compatibility-alias", canonical_operation_id="health_ready"
    ),
    _operation("post", "/v1/verify", "verification_create"),
    _operation(
        "post",
        "/verify",
        "verification_create_compatibility",
        "compatibility-alias",
        canonical_operation_id="verification_create",
    ),
    _operation("get", "/v1/capabilities", "capabilities_get"),
    _operation("get", "/v1/auth/csrf", "browser_csrf_get", "browser-only"),
    _operation("get", "/v1/auth/{provider}", "browser_authorization_start", "browser-only"),
    _operation("get", "/v1/auth/{provider}/callback", "browser_authorization_callback", "browser-only"),
    _operation("post", "/v1/auth/logout", "browser_session_revoke", "browser-only"),
    _operation("get", "/v1/builds", "builds_list"),
    _operation("post", "/v1/builds", "builds_create", "browser-only"),
    _operation("get", "/v1/builds/{build_id}", "builds_get"),
    _operation("patch", "/v1/builds/{build_id}", "builds_update", "browser-only"),
    _operation(
        "post",
        "/v1/cli/auth/enrollments",
        "cli_enrollment_start",
        "command",
        command="auth.login",
        features=("cli-device-auth",),
        interaction="browser-continuation",
    ),
    _operation("post", "/v1/cli/auth/enrollments/exchange", "cli_enrollment_exchange"),
    _operation("get", "/v1/cli/auth/enrollments/approval", "cli_enrollment_preview", "browser-only"),
    _operation("post", "/v1/cli/auth/enrollments/approval", "cli_enrollment_approve", "browser-only"),
    _operation("post", "/v1/cli/auth/session-challenges", "cli_session_challenge_start"),
    _operation("post", "/v1/cli/auth/sessions", "cli_session_exchange"),
    _operation("get", "/v1/cli/auth/devices", "cli_devices_list", "browser-only"),
    _operation("delete", "/v1/cli/auth/devices/{device_id}", "cli_device_revoke", "browser-only"),
    _operation(
        "delete",
        "/v1/cli/auth/sessions/current",
        "cli_session_revoke",
        "command",
        command="auth.logout",
        features=("cli-device-auth",),
        interaction="direct",
    ),
    _operation("get", "/v1/users/me", "account_get", "browser-only"),
    _operation("post", "/v1/users/me/consent", "account_consent_grant", "browser-only"),
    _operation("get", "/v1/users/me/builds", "account_builds_list", "browser-only"),
    # A CLI command rather than browser-only: a rename is noticed in game, and the caller
    # reaching for this is as likely to be holding a CLI device credential as a browser session.
    _operation(
        "post",
        "/v1/users/me/minecraft/refresh",
        "account_minecraft_refresh",
        "command",
        command="account.refresh",
        interaction="direct",
    ),
    _operation(
        "post",
        "/v1/accounts/{account_id}/minecraft/refresh",
        "account_minecraft_refresh_for",
        "browser-only",
    ),
    _operation("get", "/v1/minecraft/auth/paper/installations", "paper_installations_list", "browser-only"),
    _operation("post", "/v1/minecraft/auth/paper/installations", "paper_installation_create", "browser-only"),
    _operation(
        "post",
        "/v1/minecraft/auth/paper/installations/{installation_id}/rotate",
        "paper_installation_rotate",
        "browser-only",
    ),
    _operation(
        "put",
        "/v1/minecraft/auth/paper/installations/{installation_id}/profile",
        "paper_installation_profile_update",
        "browser-only",
    ),
    _operation(
        "delete",
        "/v1/minecraft/auth/paper/installations/{installation_id}",
        "paper_installation_revoke",
        "browser-only",
    ),
    _operation("post", "/v1/minecraft/auth/paper/challenges", "paper_challenge_start"),
    _operation("post", "/v1/minecraft/auth/paper/challenges/exchange", "paper_challenge_exchange"),
    _operation("post", "/v1/minecraft/auth/fabric/challenges", "fabric_challenge_start"),
    _operation("post", "/v1/minecraft/auth/fabric/challenges/exchange", "fabric_challenge_exchange"),
    _operation("post", "/v1/minecraft/auth/challenges/approval", "minecraft_challenge_approve", "browser-only"),
    _operation("delete", "/v1/minecraft/auth/grants/{grant_id}", "minecraft_grant_revoke", "browser-only"),
    _operation("get", "/v1/users/me/notifications/preferences", "notification_preferences_get", "browser-only"),
    _operation("patch", "/v1/users/me/notifications/preferences", "notification_preferences_update", "browser-only"),
    _operation("post", "/v1/users/me/notifications/consent", "notification_consent_grant", "browser-only"),
    _operation("get", "/v1/users/me/notifications/subscriptions", "notification_subscriptions_list", "browser-only"),
    _operation("post", "/v1/users/me/notifications/subscriptions", "notification_subscription_create", "browser-only"),
    _operation(
        "delete",
        "/v1/users/me/notifications/subscriptions/{subscription_id}",
        "notification_subscription_delete",
        "browser-only",
    ),
    _operation("get", "/v1/users/me/notifications/inbox", "notification_inbox_list", "browser-only"),
    _operation(
        "post",
        "/v1/users/me/notifications/inbox/{notification_id}/read",
        "notification_inbox_mark_read",
        "browser-only",
    ),
    _operation("get", "/v1/records/{record_id}", "records_get"),
    _operation("get", "/v1/records", "records_list"),
    _operation("get", "/v1/builds/{build_id}/schematics", "build_schematics_list"),
    _operation("get", "/v1/builds/{build_id}/schematics/{schematic_id}/content", "build_schematic_content_get"),
    _operation("get", "/v1/schematic-renders/{recipe_hash}/content", "schematic_render_content_get"),
    _operation("get", "/v1/search/fields", "search_fields_list"),
    _operation("get", "/v1/search/suggest", "search_terms_suggest"),
    _operation("get", "/v1/search", "search_execute"),
    _operation("get", "/v1/suggest", "suggestion_sources_list"),
    _operation("get", "/v1/suggest/{source}", "suggestions_get"),
    _operation(
        "get",
        "/v1/submissions/drafts",
        "submission_drafts_list",
        "command",
        command="draft.list",
        features=("submission-drafts",),
        interaction="direct",
    ),
    _operation(
        "post",
        "/v1/submissions/drafts",
        "submission_draft_create",
        "command",
        command="draft.create",
        features=("submission-drafts",),
        interaction="direct",
    ),
    _operation("get", "/v1/submissions/form/current", "submission_form_current"),
    _operation("get", "/v1/submissions/form/schemas/{schema_id}/revisions/{revision}", "submission_form_revision_get"),
    _operation("get", "/v1/submissions/form/options/{source}", "submission_form_options_get"),
    _operation(
        "get",
        "/v1/submissions/drafts/{draft_id}",
        "submission_draft_get",
        "command",
        command="draft.show",
        features=("submission-drafts",),
        interaction="direct",
    ),
    _operation(
        "delete",
        "/v1/submissions/drafts/{draft_id}",
        "submission_draft_delete",
        "command",
        command="draft.delete",
        features=("submission-drafts",),
        interaction="direct",
    ),
    _operation(
        "post",
        "/v1/submissions/drafts/{draft_id}/changes",
        "submission_draft_change",
        "command",
        command="draft.change",
        features=("submission-drafts",),
        interaction="direct",
    ),
    _operation(
        "get",
        "/v1/submissions/drafts/{draft_id}/submission",
        "submission_finalization_get",
        "command",
        command="draft.status",
        features=("submission-finalization",),
        interaction="direct",
    ),
    _operation(
        "post",
        "/v1/submissions/drafts/{draft_id}/submission",
        "submission_finalization_start",
        "command",
        command="draft.submit",
        features=("submission-finalization",),
        interaction="direct",
    ),
    _operation(
        "post",
        "/v1/submissions/drafts/{draft_id}/media/{kind}",
        "submission_media_upload",
        "command",
        command="media.upload",
        features=("submission-media",),
        interaction="direct",
    ),
    _operation(
        "get",
        "/v1/submissions/drafts/{draft_id}/media",
        "submission_media_list",
        "command",
        command="media.list",
        features=("submission-media",),
        interaction="direct",
    ),
    _operation(
        "get",
        "/v1/submissions/drafts/{draft_id}/media/{upload_id}",
        "submission_media_get",
        "command",
        command="media.status",
        features=("submission-media",),
        interaction="direct",
    ),
    _operation(
        "delete",
        "/v1/submissions/drafts/{draft_id}/media/{upload_id}",
        "submission_media_discard",
        "command",
        command="media.discard",
        features=("submission-media",),
        interaction="direct",
    ),
    _operation("get", "/v1/tags", "tags_list"),
    _operation("get", "/v1/tags/{tag_id}", "tags_get"),
    _operation("get", "/v1/creator-aliases/{name}", "creator_alias_get"),
    _operation("get", "/v1/creators/{creator_id}", "creator_profile_get"),
    _operation("get", "/v1/versions", "minecraft_versions_list"),
    _operation("get", "/v1/vote-sessions/{vote_session_id}", "vote_session_get"),
    _operation("post", "/v1/vote-sessions/{vote_session_id}/votes", "vote_cast", "browser-only"),
)

_UNSAFE_METHODS = frozenset({"post", "put", "patch", "delete"})
_ANONYMOUS = {}
_SERVICE = {"ApiCredential": []}
_WEB = {"WebSession": []}
_WEB_WRITE = {"WebSession": [], "CsrfToken": []}
_DEVICE = {"DeviceSession": []}
_MINECRAFT = {"MinecraftPlayer": []}
_PAPER = {"PaperInstallationId": [], "PaperInstallationSecret": []}

_BROWSER_ONLY = frozenset(contract.operation_id for contract in OPERATIONS if contract.classification == "browser-only")
_DRAFTS = frozenset(
    contract.operation_id for contract in OPERATIONS if contract.operation_id.startswith("submission_draft")
)
_MEDIA = frozenset(
    contract.operation_id for contract in OPERATIONS if contract.operation_id.startswith("submission_media")
)
_FINALIZATION = frozenset(
    contract.operation_id for contract in OPERATIONS if contract.operation_id.startswith("submission_finalization")
)
_PUBLIC_MUTATIONS = frozenset(
    {
        "cli_enrollment_start",
        "cli_enrollment_exchange",
        "cli_session_challenge_start",
        "cli_session_exchange",
        "fabric_challenge_start",
        "fabric_challenge_exchange",
    }
)
_ANONYMOUS_BROWSER = frozenset({"browser_authorization_start", "browser_authorization_callback"})
_PAPER_OPERATIONS = frozenset({"paper_challenge_start", "paper_challenge_exchange"})
_DEVICE_ONLY = frozenset({"cli_session_revoke"})
_OPTIONAL_PRINCIPAL = frozenset({"builds_list", "vote_session_get"})
_VERIFY = frozenset({"verification_create", "verification_create_compatibility"})

_SCOPES = {
    "verification_create": ("account.verify.relay",),
    "verification_create_compatibility": ("account.verify.relay",),
}
"""Permission nodes a credential must carry, published in the contract.

Still `x-required-api-scopes` in the document: the field is part of the public
contract and renaming it would break consumers for a vocabulary change they do
not need to care about."""

_DRAFT_RESPONSE_LINKS: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("submission_draft_create", "201"): {
        "GetCreatedDraft": {
            "operationId": "submission_draft_get",
            "parameters": {"draft_id": "$response.body#/id"},
        },
        "ChangeCreatedDraft": {
            "operationId": "submission_draft_change",
            "parameters": {"draft_id": "$response.body#/id"},
        },
        "FinalizeCreatedDraft": {
            "operationId": "submission_finalization_start",
            "parameters": {"draft_id": "$response.body#/id"},
        },
        "DeleteCreatedDraft": {
            "operationId": "submission_draft_delete",
            "parameters": {"draft_id": "$response.body#/id"},
        },
    },
    ("submission_draft_change", "200"): {
        "ChangeDraftAgain": {
            "operationId": "submission_draft_change",
            "parameters": {"draft_id": "$response.body#/draft/id"},
        },
        "FinalizeChangedDraft": {
            "operationId": "submission_finalization_start",
            "parameters": {"draft_id": "$response.body#/draft/id"},
        },
    },
    ("submission_finalization_start", "202"): {
        "GetFinalization": {
            "operationId": "submission_finalization_get",
            "parameters": {"draft_id": "$response.body#/draft_id"},
        },
        "DeleteFinalizedDraft": {
            "operationId": "submission_draft_delete",
            "parameters": {"draft_id": "$response.body#/draft_id"},
        },
    },
    ("submission_draft_delete", "204"): {
        "UseAfterDeletedDraft": {
            "operationId": "submission_draft_get",
            "parameters": {"draft_id": "$request.path.draft_id"},
            "description": "Use-after-free check for a deleted draft identifier.",
        },
    },
}


def install_openapi_contract(app: FastAPI) -> None:
    """Install deterministic schema generation with permanent operation metadata."""

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
    for contract in OPERATIONS:
        operation = document["paths"][contract.path][contract.method]
        operation["operationId"] = contract.operation_id
        operation["security"] = _security(contract)
        operation["x-squid-cli"] = _cli_metadata(contract)
        _install_response_links(operation, contract)
        _install_request_id_header(operation)
        if scopes := _SCOPES.get(contract.operation_id):
            operation["x-required-api-scopes"] = list(scopes)


def _cli_metadata(contract: OperationContract) -> dict[str, Any]:
    metadata: dict[str, Any] = {"classification": contract.classification}
    if contract.classification == "command":
        metadata.update(
            command=contract.command,
            required_api_features=list(contract.features),
            interaction=contract.interaction,
        )
    elif contract.classification == "compatibility-alias":
        metadata["canonical_operation_id"] = contract.canonical_operation_id
    elif contract.rationale is not None:
        metadata["rationale"] = contract.rationale
    return metadata


def _security(contract: OperationContract) -> list[SecurityRequirement]:
    operation_id = contract.operation_id
    if operation_id.startswith("health_") or operation_id == "capabilities_get" or operation_id in _ANONYMOUS_BROWSER:
        return [_ANONYMOUS]
    if operation_id in _PUBLIC_MUTATIONS:
        return [_ANONYMOUS]
    if operation_id in _PAPER_OPERATIONS:
        return [_PAPER]
    if operation_id in _DEVICE_ONLY:
        return [_DEVICE]
    if operation_id in _VERIFY:
        return [_SERVICE, _WEB_WRITE, _DEVICE]
    if operation_id in _DRAFTS | _MEDIA | _FINALIZATION:
        web = _WEB_WRITE if contract.method in _UNSAFE_METHODS else _WEB
        return [web, _DEVICE, _MINECRAFT]
    if operation_id in _BROWSER_ONLY:
        return [_WEB_WRITE if contract.method in _UNSAFE_METHODS else _WEB]
    if operation_id in _OPTIONAL_PRINCIPAL:
        return [_ANONYMOUS, _SERVICE, _WEB, _DEVICE]
    return [_ANONYMOUS]


def _install_response_links(operation: dict[str, Any], contract: OperationContract) -> None:
    for (operation_id, status_code), links in _DRAFT_RESPONSE_LINKS.items():
        if operation_id != contract.operation_id:
            continue
        response = operation["responses"][status_code]
        response["links"] = links


def _install_request_id_header(operation: dict[str, Any]) -> None:
    """Declare the Request-Id correlation header the middleware emits on every response."""
    for response in operation["responses"].values():
        response.setdefault("headers", {})["Request-Id"] = {"$ref": "#/components/headers/RequestId"}


def validate_operation_manifest() -> None:
    """Raise when permanent contract metadata is internally inconsistent."""
    identifiers = {contract.operation_id for contract in OPERATIONS}
    commands = [contract.command for contract in OPERATIONS if contract.classification == "command"]
    if len(identifiers) != len(OPERATIONS) or len(commands) != len(set(commands)):
        msg = "OpenAPI operation and CLI command mappings must be unique."
        raise ValueError(msg)
    for contract in OPERATIONS:
        if not set(contract.features) <= API_FEATURES:
            msg = f"Unknown API feature in {contract.operation_id}."
            raise ValueError(msg)
        if contract.classification == "compatibility-alias" and contract.canonical_operation_id not in identifiers:
            msg = f"Unknown canonical operation for {contract.operation_id}."
            raise ValueError(msg)
        if contract.canonical_operation_id is not None:
            target = next(item for item in OPERATIONS if item.operation_id == contract.canonical_operation_id)
            if target.classification == "compatibility-alias":
                msg = f"Chained compatibility alias at {contract.operation_id}."
                raise ValueError(msg)
    for links in _DRAFT_RESPONSE_LINKS.values():
        for link in links.values():
            linked_operation = link.get("operationId")
            if linked_operation not in identifiers:
                msg = f"Unknown OpenAPI response link target: {linked_operation}."
                raise ValueError(msg)


validate_operation_manifest()
