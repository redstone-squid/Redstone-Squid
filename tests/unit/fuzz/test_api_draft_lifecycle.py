"""Submission-draft lifecycle model tests without starting the API."""

from uuid import UUID

from tests.fuzz.api.draft_lifecycle import (
    DEFAULT_CHANGE_IDEMPOTENCY_KEY,
    DEFAULT_CLIENT_INSTANCE_ID,
    DEFAULT_CSRF_TOKEN,
    DEFAULT_STALE_IDEMPOTENCY_KEY,
    DRAFT_OPERATION_IDS,
    DRAFT_PRODUCER_LINKS,
    DraftLifecycleScenario,
    DraftRequest,
    DraftResponse,
    DraftStage,
    DraftWebAuth,
    change_draft_body,
    create_draft_body,
)
from tests.fuzz.api.environment import SeededIds

DRAFT_ID = "00000000-0000-0000-0000-00000000da7a"


class FakeDraftClient:
    """Return the deterministic lifecycle responses and record emitted requests."""

    def __init__(self) -> None:
        self.requests: list[DraftRequest] = []

    def send(self, request: DraftRequest) -> DraftResponse:
        self.requests.append(request)
        match [request.operation_id, len(self.requests)]:
            case ["submission_draft_create", 1]:
                return DraftResponse(201, _draft(revision=0, answers={}))
            case ["submission_draft_get", 2]:
                return DraftResponse(200, _draft(revision=0, answers={}))
            case ["submission_draft_change", 3]:
                return DraftResponse(
                    200,
                    {"draft": _draft(revision=1, answers={"display_name": "Alice fuzz draft"}), "replayed": False},
                )
            case ["submission_draft_change", 4]:
                return DraftResponse(
                    200,
                    {"draft": _draft(revision=1, answers={"display_name": "Alice fuzz draft"}), "replayed": True},
                )
            case ["submission_draft_change", 5]:
                return DraftResponse(409, {"type": "about:blank", "title": "Draft changed"})
            case ["submission_finalization_start", 6]:
                return DraftResponse(
                    202,
                    {
                        "draft_id": DRAFT_ID,
                        "draft_revision": 1,
                        "status": "needs_attention",
                        "issues": [{"field_id": "description", "reason": "required"}],
                        "build_id": None,
                    },
                )
            case ["submission_finalization_get", 7]:
                return DraftResponse(
                    200,
                    {
                        "draft_id": DRAFT_ID,
                        "draft_revision": 1,
                        "status": "needs_attention",
                        "issues": [{"field_id": "description", "reason": "required"}],
                        "build_id": None,
                    },
                )
            case ["submission_draft_delete", 8]:
                return DraftResponse(204)
            case ["submission_draft_get", 9]:
                return DraftResponse(404, {"type": "about:blank", "title": "Draft not found"})
        raise AssertionError(request)


def test_create_and_change_bodies_are_deterministic_and_contract_valid() -> None:
    assert create_draft_body() == {
        "category": "other",
        "origin": "web",
        "client_capabilities": ["repeatable_text"],
    }
    assert change_draft_body(base_revision=0, idempotency_key=DEFAULT_CHANGE_IDEMPOTENCY_KEY) == {
        "base_revision": 0,
        "client_instance_id": DEFAULT_CLIENT_INSTANCE_ID,
        "idempotency_key": DEFAULT_CHANGE_IDEMPOTENCY_KEY,
        "operations": [
            {
                "operation_id": "00000000-0000-0000-0000-00000000d001",
                "field_id": "display_name",
                "kind": "set",
                "value": "Alice fuzz draft",
            }
        ],
    }


def test_alice_web_auth_uses_seeded_session_and_double_submit_csrf() -> None:
    auth = DraftWebAuth.alice(seeded_ids())

    assert auth.cookies == {"__Host-squid_session": "alice-session", "squid_csrf": DEFAULT_CSRF_TOKEN}
    assert auth.write_headers == {"X-CSRF-Token": DEFAULT_CSRF_TOKEN}
    assert "alice-session" not in repr(auth)


def test_deterministic_lifecycle_runs_every_expected_transition() -> None:
    client = FakeDraftClient()
    scenario = DraftLifecycleScenario(client, DraftWebAuth("alice-session"))

    scenario.run_once()

    assert scenario.state.stage is DraftStage.USE_AFTER_FREE_CHECKED
    assert scenario.state.draft_id == DRAFT_ID
    assert scenario.state.revision == 1
    assert [request.operation_id for request in client.requests] == [
        "submission_draft_create",
        "submission_draft_get",
        "submission_draft_change",
        "submission_draft_change",
        "submission_draft_change",
        "submission_finalization_start",
        "submission_finalization_get",
        "submission_draft_delete",
        "submission_draft_get",
    ]
    assert client.requests[3].json == change_draft_body(
        base_revision=0,
        idempotency_key=DEFAULT_CHANGE_IDEMPOTENCY_KEY,
    )
    assert client.requests[4].json == change_draft_body(
        base_revision=0,
        idempotency_key=DEFAULT_STALE_IDEMPOTENCY_KEY,
        operation_id=UUID("00000000-0000-0000-0000-00000000d002"),
        value="stale value",
    )


def test_draft_producer_links_cover_only_known_draft_operations() -> None:
    operations = set(DRAFT_OPERATION_IDS)
    for link in DRAFT_PRODUCER_LINKS:
        assert link.producer_operation_id in operations
        assert link.target_operation_id in operations
    assert {(link.producer_operation_id, link.name) for link in DRAFT_PRODUCER_LINKS} == {
        ("submission_draft_create", "GetCreatedDraft"),
        ("submission_draft_create", "ChangeCreatedDraft"),
        ("submission_draft_create", "FinalizeCreatedDraft"),
        ("submission_draft_create", "DeleteCreatedDraft"),
        ("submission_draft_change", "ChangeDraftAgain"),
        ("submission_draft_change", "FinalizeChangedDraft"),
        ("submission_finalization_start", "GetFinalization"),
        ("submission_finalization_start", "DeleteFinalizedDraft"),
        ("submission_draft_delete", "UseAfterDeletedDraft"),
    }


def _draft(*, revision: int, answers: dict[str, object]) -> dict[str, object]:
    return {
        "id": DRAFT_ID,
        "schema_id": "redstone_squid_v1",
        "schema_revision": 1,
        "category": "other",
        "revision": revision,
        "status": "editing",
        "answers": answers,
        "origin": "web",
        "created_at": "2026-08-04T00:00:00Z",
        "updated_at": "2026-08-04T00:00:00Z",
        "expires_at": "2100-01-01T00:00:00Z",
    }


def seeded_ids() -> SeededIds:
    return SeededIds(
        alice_account_id=1,
        bob_account_id=2,
        consent_pending_account_id=3,
        administrator_account_id=4,
        java_version_id=1,
        alice_public_id="alice",
        bob_public_id="bob",
        alice_web_session="alice-session",
        bob_web_session="bob-session",
        consent_pending_web_session="pending-session",
        administrator_web_session="admin-session",
        service_api_token="api-token",
    )
