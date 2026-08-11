"""Deterministic audits for the canonical HTTP API contract."""

import json
from pathlib import Path

import httpx

from squid.api.capabilities import API_FEATURES
from squid.api.errors import ProblemDetail
from squid.api.openapi import OPERATIONS
from tests.fuzz.api.draft_lifecycle import DRAFT_PRODUCER_LINKS
from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
OPENAPI_DOCUMENT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"


# Locale negotiation (squid/api/i18n.py) sits in front of every response, including error
# responses generated from schema-conformant-but-invalid requests. Fuzz Accept-Language
# alongside the generated request to make sure header parsing itself never 500s, and that
# ProblemDetail's title/detail stay non-empty strings regardless of what locale was requested.
def test_api_never_errors_on_accept_language(client: httpx.Client) -> None:
    for accept_language in ("en", "zh-CN", "zh-TW", "fr-FR;q=0.9,*;q=0.1", "not-a-locale-tag", ""):
        response = client.post(
            "/verify",
            json={"uuid": "not-a-uuid"},
            headers={"Authorization": TEST_SYNERGY_SECRET, "Accept-Language": accept_language},
        )
        assert response.status_code < 500
        problem = response.json()
        assert isinstance(problem["title"], str)
        assert problem["title"]
        assert isinstance(problem["detail"], str)
        assert problem["detail"]


def test_catalogue_extensions_are_registered_in_openapi() -> None:
    document = _app.openapi()
    schemas = document["components"]["schemas"]

    assert {"preview", "version_spec", "versions", "opening_time", "closing_time"} <= set(
        schemas["BuildSummary"]["properties"]
    )
    assert "key" in schemas["BuildTag"]["properties"]
    assert "holder_builds" in schemas["RecordDetail"]["properties"]
    assert "500" in document["paths"]["/v1/records/{record_id}"]["get"]["responses"]


def test_every_mutating_operation_accepts_an_idempotency_key() -> None:
    document = _app.openapi()
    streaming_retries = {("/v1/submissions/drafts/{draft_id}/media/{kind}", "post")}

    for path, path_item in document["paths"].items():
        for method in ("post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
            if (path, method) in streaming_retries:
                assert any(
                    parameter.get("in") == "query" and parameter.get("name") == "upload_id" for parameter in parameters
                ), f"{method.upper()} {path} lacks its streaming-safe retry UUID"
                continue
            assert any(
                parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key"
                for parameter in parameters
            ), f"{method.upper()} {path} does not declare Idempotency-Key"


def test_committed_openapi_document_matches_application() -> None:
    committed = json.loads(OPENAPI_DOCUMENT.read_text(encoding="utf-8"))

    assert committed == _app.openapi()


def test_every_operation_has_stable_cli_and_security_metadata() -> None:
    document = _app.openapi()
    expected_locations = {(contract.path, contract.method) for contract in OPERATIONS}
    actual_locations = {
        (path, method)
        for path, path_item in document["paths"].items()
        for method in ("get", "post", "put", "patch", "delete")
        if method in path_item
    }
    assert actual_locations == expected_locations

    identifiers: list[str] = []
    command_paths: list[str] = []
    for contract in OPERATIONS:
        operation = document["paths"][contract.path][contract.method]
        assert operation["operationId"] == contract.operation_id
        assert operation["security"]
        assert operation["x-squid-cli"]["classification"] == contract.classification
        identifiers.append(operation["operationId"])
        if contract.classification == "command":
            metadata = operation["x-squid-cli"]
            assert metadata["interaction"] in {"direct", "browser-continuation"}
            assert set(metadata["required_api_features"]) <= API_FEATURES
            command_paths.append(metadata["command"])

    assert len(identifiers) == len(set(identifiers))
    assert len(command_paths) == len(set(command_paths))


def test_openapi_declares_authentication_alternatives_and_scopes() -> None:
    document = _app.openapi()
    schemes = document["components"]["securitySchemes"]
    assert {"ApiCredential", "WebSession", "CsrfToken", "DeviceSession"} <= schemes.keys()
    assert document["paths"]["/v1/capabilities"]["get"]["security"] == [{}]
    assert document["paths"]["/v1/auth/logout"]["post"]["security"] == [{"WebSession": [], "CsrfToken": []}]
    assert document["paths"]["/v1/cli/auth/sessions/current"]["delete"]["security"] == [{"DeviceSession": []}]
    assert document["paths"]["/v1/verify"]["post"]["x-required-api-scopes"] == ["verify"]


def test_openapi_declares_submission_draft_producer_links() -> None:
    document = _app.openapi()
    operation_locations = {contract.operation_id: (contract.path, contract.method) for contract in OPERATIONS}

    for expected in DRAFT_PRODUCER_LINKS:
        path, method = operation_locations[expected.producer_operation_id]
        link = document["paths"][path][method]["responses"][expected.status_code]["links"][expected.name]

        assert link["operationId"] == expected.target_operation_id
        assert link["parameters"] == {"draft_id": expected.draft_id_expression}


def test_cli_command_operations_have_language_neutral_fixtures() -> None:
    fixture_path = OPENAPI_DOCUMENT.parent / "fixtures" / "cli-operations.json"
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    commands = {contract.operation_id for contract in OPERATIONS if contract.classification == "command"}

    assert fixtures["version"] == 1
    assert set(fixtures["operations"]) == commands
    for operation_id, fixture in fixtures["operations"].items():
        assert fixture["success"]["status"] in {200, 201, 202, 204}
        assert fixture["problem"]["status"] >= 400
        problem = ProblemDetail.model_validate(fixture["problem"]["body"])
        assert problem.status == fixture["problem"]["status"]
        assert problem.code is not None
        if operation_id == "submission_media_upload":
            assert fixture["request"]["headers"]["Content-Type"] == "image/png"
            assert fixture["request"]["boundary"] == "raw request body is the exact file byte sequence"
