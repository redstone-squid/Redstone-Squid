"""Deterministic audits for the canonical HTTP API contract."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from scripts import export_openapi
from squid.api.app import create_api_app
from squid.api.capabilities import API_FEATURES
from squid.api.contract import ANONYMOUS, contract, transport_only, validate_contract
from squid.api.errors import ProblemDetail
from tests.fuzz.api.draft_lifecycle import DRAFT_PRODUCER_LINKS
from tests.unit.api.fakes import TEST_SYNERGY_SECRET, build_app

_app, _database = build_app()
OPENAPI_DOCUMENT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _document_operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return every (path, method, operation) triple for a schema-included HTTP operation."""
    return [
        (path, method, operation)
        for path, path_item in document["paths"].items()
        for method in _HTTP_METHODS
        if (operation := path_item.get(method)) is not None
    ]


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


def test_openapi_export_is_cwd_independent_and_byte_deterministic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "generated" / "openapi.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(export_openapi, "OUTPUT_PATH", destination)

    export_openapi.main()

    expected = create_api_app().openapi()
    expected_bytes = (json.dumps(expected, ensure_ascii=False, indent=2) + "\n").encode()
    assert destination.read_bytes() == expected_bytes
    assert json.loads(destination.read_text(encoding="utf-8")) == expected
    assert export_openapi.PROJECT_ROOT == Path(__file__).resolve().parents[3]


_CLASSIFICATIONS = frozenset({"command", "browser-only", "transport-only", "internal", "compatibility-alias"})


def test_every_operation_has_stable_cli_and_security_metadata() -> None:
    document = _app.openapi()

    identifiers: list[str] = []
    command_paths: list[str] = []
    for _path, _method, operation in _document_operations(document):
        assert operation["operationId"]
        assert operation["security"]
        cli = operation["x-squid-cli"]
        assert cli["classification"] in _CLASSIFICATIONS
        identifiers.append(operation["operationId"])
        if cli["classification"] == "command":
            assert cli["interaction"] in {"direct", "browser-continuation"}
            assert set(cli["required_api_features"]) <= API_FEATURES
            command_paths.append(cli["command"])

    assert len(identifiers) == len(set(identifiers))
    assert len(command_paths) == len(set(command_paths))


def test_every_operation_response_declares_the_request_id_header() -> None:
    document = _app.openapi()
    assert document["components"]["headers"]["RequestId"]["schema"]["pattern"] == "^[A-Za-z0-9._-]{8,128}$"

    for path, method, operation in _document_operations(document):
        for status_code, response in operation["responses"].items():
            assert response["headers"]["Request-Id"] == {"$ref": "#/components/headers/RequestId"}, (
                f"{method.upper()} {path} {status_code} lacks the Request-Id header"
            )


def test_openapi_declares_authentication_alternatives_and_scopes() -> None:
    document = _app.openapi()
    schemes = document["components"]["securitySchemes"]
    assert {"ApiCredential", "WebSession", "CsrfToken", "DeviceSession"} <= schemes.keys()
    assert document["paths"]["/v1/capabilities"]["get"]["security"] == [{}]
    assert document["paths"]["/v1/auth/logout"]["post"]["security"] == [{"WebSession": [], "CsrfToken": []}]
    assert document["paths"]["/v1/cli/auth/sessions/current"]["delete"]["security"] == [{"DeviceSession": []}]
    assert document["paths"]["/v1/verify"]["post"]["x-required-api-scopes"] == ["account.verify.relay"]


def test_openapi_declares_submission_draft_producer_links() -> None:
    document = _app.openapi()
    operation_locations = {
        operation["operationId"]: (path, method) for path, method, operation in _document_operations(document)
    }

    for expected in DRAFT_PRODUCER_LINKS:
        path, method = operation_locations[expected.producer_operation_id]
        link = document["paths"][path][method]["responses"][expected.status_code]["links"][expected.name]

        assert link["operationId"] == expected.target_operation_id
        assert link["parameters"] == {"draft_id": expected.draft_id_expression}


def test_validate_contract_rejects_a_route_without_an_operation_id() -> None:
    app = FastAPI()
    app.get("/bare")(lambda: {})

    with pytest.raises(ValueError, match="no explicit operation_id"):
        validate_contract(app)


def test_validate_contract_rejects_a_route_missing_contract_metadata() -> None:
    app = FastAPI()
    app.get("/bare", operation_id="bare_get")(lambda: {})

    with pytest.raises(ValueError, match="no declared contract"):
        validate_contract(app)


def test_validate_contract_accepts_a_route_declared_through_contract() -> None:
    app = FastAPI()
    app.get(
        "/bare",
        operation_id="bare_get",
        openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
    )(lambda: {})

    validate_contract(app)


def test_cli_command_operations_have_untranslated_fixtures() -> None:
    document = _app.openapi()
    fixture_path = OPENAPI_DOCUMENT.parent / "fixtures" / "cli-operations.json"
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    commands = {
        operation["operationId"]
        for _path, _method, operation in _document_operations(document)
        if operation["x-squid-cli"]["classification"] == "command"
    }

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
