"""Deterministic Alice/web submission-draft lifecycle model."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol
from uuid import UUID

from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from tests.fuzz.api.environment import SeededIds

JsonObject = dict[str, object]

DRAFT_OPERATION_IDS = (
    "submission_draft_create",
    "submission_draft_get",
    "submission_draft_change",
    "submission_finalization_start",
    "submission_finalization_get",
    "submission_draft_delete",
)
DISPLAY_NAME_OPERATION_ID = UUID("00000000-0000-0000-0000-00000000d001")
STALE_DISPLAY_NAME_OPERATION_ID = UUID("00000000-0000-0000-0000-00000000d002")
DEFAULT_CLIENT_INSTANCE_ID = "api-fuzz-alice-web"
DEFAULT_CHANGE_IDEMPOTENCY_KEY = "api-fuzz-draft-change-1"
DEFAULT_STALE_IDEMPOTENCY_KEY = "api-fuzz-draft-stale-1"
DEFAULT_CSRF_TOKEN = "api-fuzz-csrf-token"


class DraftStage(StrEnum):
    """Ordered lifecycle states in the first Alice/web draft scenario."""

    NEW = "new"
    CREATED = "created"
    CHANGED = "changed"
    REPLAYED = "replayed"
    STALE_CHECKED = "stale_checked"
    SUBMITTED = "submitted"
    STATUS_CHECKED = "status_checked"
    DELETED = "deleted"
    USE_AFTER_FREE_CHECKED = "use_after_free_checked"


@dataclass(frozen=True, slots=True)
class ProducerLink:
    """One OpenAPI response link that carries a draft identifier forward."""

    producer_operation_id: str
    status_code: str
    name: str
    target_operation_id: str
    draft_id_expression: str


DRAFT_PRODUCER_LINKS = (
    ProducerLink("submission_draft_create", "201", "GetCreatedDraft", "submission_draft_get", "$response.body#/id"),
    ProducerLink(
        "submission_draft_create",
        "201",
        "ChangeCreatedDraft",
        "submission_draft_change",
        "$response.body#/id",
    ),
    ProducerLink(
        "submission_draft_create",
        "201",
        "FinalizeCreatedDraft",
        "submission_finalization_start",
        "$response.body#/id",
    ),
    ProducerLink(
        "submission_draft_create",
        "201",
        "DeleteCreatedDraft",
        "submission_draft_delete",
        "$response.body#/id",
    ),
    ProducerLink(
        "submission_draft_change",
        "200",
        "ChangeDraftAgain",
        "submission_draft_change",
        "$response.body#/draft/id",
    ),
    ProducerLink(
        "submission_draft_change",
        "200",
        "FinalizeChangedDraft",
        "submission_finalization_start",
        "$response.body#/draft/id",
    ),
    ProducerLink(
        "submission_finalization_start",
        "202",
        "GetFinalization",
        "submission_finalization_get",
        "$response.body#/draft_id",
    ),
    ProducerLink(
        "submission_finalization_start",
        "202",
        "DeleteFinalizedDraft",
        "submission_draft_delete",
        "$response.body#/draft_id",
    ),
    ProducerLink(
        "submission_draft_delete",
        "204",
        "UseAfterDeletedDraft",
        "submission_draft_get",
        "$request.path.draft_id",
    ),
)


@dataclass(frozen=True, slots=True)
class DraftWebAuth:
    """Alice's browser-session credential and double-submit CSRF token."""

    session_token: str = field(repr=False)
    csrf_token: str = field(default=DEFAULT_CSRF_TOKEN, repr=False)

    @classmethod
    def alice(cls, seeded_ids: SeededIds) -> "DraftWebAuth":
        """Build Alice's web credentials from deterministic seed data."""
        return cls(session_token=seeded_ids.alice_web_session)

    @property
    def cookies(self) -> dict[str, str]:
        """Return browser cookies required by the API security dependency."""
        return {"__Host-squid_session": self.session_token, "squid_csrf": self.csrf_token}

    @property
    def write_headers(self) -> dict[str, str]:
        """Return write headers required by the API security dependency."""
        return {"CSRF-Token": self.csrf_token}


@dataclass(frozen=True, slots=True)
class DraftRequest:
    """One exact HTTP request emitted by the draft lifecycle model."""

    operation_id: str
    method: str
    path: str
    json: JsonObject | None = None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    cookies: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class DraftResponse:
    """Minimal response facts consumed by the deterministic lifecycle reducer."""

    status_code: int
    json: JsonObject | None = None


class DraftLifecycleClient(Protocol):
    """Synchronous transport boundary used by the draft state machine."""

    def send(self, request: DraftRequest) -> DraftResponse:
        """Send one request and return bounded response facts."""


@dataclass(slots=True)
class DraftLifecycleState:
    """Current reducer state for the Alice/web draft lifecycle."""

    stage: DraftStage = DraftStage.NEW
    draft_id: str | None = None
    revision: int = 0
    finalization_status: str | None = None


@dataclass(slots=True)
class DraftLifecycleScenario:
    """Execute the first deterministic draft lifecycle over a narrow client protocol."""

    client: DraftLifecycleClient
    auth: DraftWebAuth
    state: DraftLifecycleState = field(default_factory=DraftLifecycleState)

    def create(self) -> None:
        """Create an empty Alice/web draft."""
        response = self.client.send(create_draft_request(self.auth))
        body = _expect_json(response, 201)
        self.state.draft_id = _expect_str(body, "id")
        _expect_field(body, "category", "other")
        _expect_field(body, "origin", "web")
        _expect_field(body, "revision", 0)
        _expect_field(body, "status", "editing")
        _expect_field(body, "answers", {})
        self.state.stage = DraftStage.CREATED

    def get_created(self) -> None:
        """Read the current draft snapshot."""
        response = self.client.send(get_draft_request(self.auth, self._draft_id()))
        body = _expect_json(response, 200)
        _expect_field(body, "id", self._draft_id())
        _expect_field(body, "revision", self.state.revision)
        _expect_field(body, "status", "editing")

    def change_display_name(self) -> None:
        """Apply the first optimistic display-name edit."""
        response = self.client.send(
            change_draft_request(self.auth, self._draft_id(), self.state.revision, DEFAULT_CHANGE_IDEMPOTENCY_KEY)
        )
        body = _expect_json(response, 200)
        _expect_field(body, "replayed", expected=False)
        draft = _expect_object(body, "draft")
        _expect_field(draft, "id", self._draft_id())
        _expect_field(draft, "revision", self.state.revision + 1)
        _expect_field(draft, "status", "editing")
        _expect_field(draft, "answers", {"display_name": "Alice fuzz draft"})
        self.state.revision += 1
        self.state.stage = DraftStage.CHANGED

    def replay_change(self) -> None:
        """Replay the exact same change and require native idempotency."""
        response = self.client.send(
            change_draft_request(self.auth, self._draft_id(), 0, DEFAULT_CHANGE_IDEMPOTENCY_KEY)
        )
        body = _expect_json(response, 200)
        _expect_field(body, "replayed", expected=True)
        draft = _expect_object(body, "draft")
        _expect_field(draft, "id", self._draft_id())
        _expect_field(draft, "revision", self.state.revision)
        self.state.stage = DraftStage.REPLAYED

    def stale_conflict(self) -> None:
        """Apply a new change against revision zero and require a stable conflict."""
        response = self.client.send(
            change_draft_request(
                self.auth,
                self._draft_id(),
                0,
                DEFAULT_STALE_IDEMPOTENCY_KEY,
                operation_id=STALE_DISPLAY_NAME_OPERATION_ID,
                value="stale value",
            )
        )
        if response.status_code != 409:
            msg = f"Expected stale draft conflict, got {response.status_code}."
            raise AssertionError(msg)
        self.state.stage = DraftStage.STALE_CHECKED

    def submit_incomplete(self) -> None:
        """Submit the intentionally incomplete draft and require needs-attention finalization."""
        response = self.client.send(submit_draft_request(self.auth, self._draft_id()))
        body = _expect_json(response, 202)
        _expect_field(body, "draft_id", self._draft_id())
        _expect_field(body, "draft_revision", self.state.revision)
        _expect_field(body, "status", "needs_attention")
        _expect_field(body, "build_id", None)
        issues = body.get("issues")
        if not isinstance(issues, list) or not issues:
            msg = "Expected incomplete draft submission to return at least one attention issue."
            raise AssertionError(msg)
        self.state.finalization_status = "needs_attention"
        self.state.stage = DraftStage.SUBMITTED

    def get_finalization(self) -> None:
        """Read retained finalization status."""
        response = self.client.send(get_finalization_request(self.auth, self._draft_id()))
        body = _expect_json(response, 200)
        _expect_field(body, "draft_id", self._draft_id())
        _expect_field(body, "draft_revision", self.state.revision)
        _expect_field(body, "status", self.state.finalization_status)
        self.state.stage = DraftStage.STATUS_CHECKED

    def delete(self) -> None:
        """Delete the draft after the needs-attention finalization path."""
        response = self.client.send(delete_draft_request(self.auth, self._draft_id()))
        if response.status_code != 204:
            msg = f"Expected draft delete to return 204, got {response.status_code}."
            raise AssertionError(msg)
        self.state.stage = DraftStage.DELETED

    def get_deleted(self) -> None:
        """Require use-after-free reads of the deleted draft identifier to fail."""
        response = self.client.send(get_draft_request(self.auth, self._draft_id()))
        if response.status_code != 404:
            msg = f"Expected deleted draft lookup to return 404, got {response.status_code}."
            raise AssertionError(msg)
        self.state.stage = DraftStage.USE_AFTER_FREE_CHECKED

    def run_once(self) -> None:
        """Execute the complete deterministic lifecycle in the intended order."""
        self.create()
        self.get_created()
        self.change_display_name()
        self.replay_change()
        self.stale_conflict()
        self.submit_incomplete()
        self.get_finalization()
        self.delete()
        self.get_deleted()

    def _draft_id(self) -> str:
        if self.state.draft_id is None:
            msg = "Draft lifecycle has no draft identifier yet."
            raise AssertionError(msg)
        return self.state.draft_id


class DraftLifecycleStateMachine(RuleBasedStateMachine):
    """Hypothesis-compatible wrapper around the deterministic draft lifecycle scenario."""

    client_factory: ClassVar[Callable[[], DraftLifecycleClient] | None] = None
    auth_factory: ClassVar[Callable[[], DraftWebAuth] | None] = None
    reset_callback: ClassVar[Callable[[], None] | None] = None

    def __init__(self) -> None:
        super().__init__()
        self.scenario: DraftLifecycleScenario | None = None

    @classmethod
    def configure(
        cls,
        *,
        client_factory: Callable[[], DraftLifecycleClient],
        auth_factory: Callable[[], DraftWebAuth],
        reset_callback: Callable[[], None],
    ) -> None:
        """Configure process-local factories before Hypothesis constructs an instance."""
        cls.client_factory = client_factory
        cls.auth_factory = auth_factory
        cls.reset_callback = reset_callback

    @initialize()
    def initialize_scenario(self) -> None:
        """Reset the external environment and create a fresh scenario model."""
        if self.client_factory is None or self.auth_factory is None or self.reset_callback is None:
            msg = "Draft lifecycle state machine factories are not configured."
            raise RuntimeError(msg)
        self.reset_callback()
        self.scenario = DraftLifecycleScenario(self.client_factory(), self.auth_factory())

    @precondition(lambda self: self._has_stage(DraftStage.NEW))
    @rule()
    def create(self) -> None:
        self._scenario().create()

    @precondition(lambda self: self._has_stage(DraftStage.CREATED))
    @rule()
    def change_display_name(self) -> None:
        self._scenario().change_display_name()

    @precondition(lambda self: self._has_stage(DraftStage.CHANGED))
    @rule()
    def replay_change(self) -> None:
        self._scenario().replay_change()

    @precondition(lambda self: self._has_stage(DraftStage.REPLAYED))
    @rule()
    def stale_conflict(self) -> None:
        self._scenario().stale_conflict()

    @precondition(lambda self: self._has_stage(DraftStage.STALE_CHECKED))
    @rule()
    def submit_incomplete(self) -> None:
        self._scenario().submit_incomplete()

    @precondition(lambda self: self._has_stage(DraftStage.SUBMITTED))
    @rule()
    def get_finalization(self) -> None:
        self._scenario().get_finalization()

    @precondition(lambda self: self._has_stage(DraftStage.STATUS_CHECKED))
    @rule()
    def delete(self) -> None:
        self._scenario().delete()

    @precondition(lambda self: self._has_stage(DraftStage.DELETED))
    @rule()
    def get_deleted(self) -> None:
        self._scenario().get_deleted()

    @invariant()
    def draft_identity_is_stable_after_creation(self) -> None:
        if self.scenario is None:
            return
        scenario = self.scenario
        if scenario.state.stage is not DraftStage.NEW and scenario.state.draft_id is None:
            msg = "Draft lifecycle lost its created draft identifier."
            raise AssertionError(msg)

    def _has_stage(self, stage: DraftStage) -> bool:
        return self.scenario is not None and self.scenario.state.stage is stage

    def _scenario(self) -> DraftLifecycleScenario:
        if self.scenario is None:
            msg = "Draft lifecycle state machine was used before initialization."
            raise RuntimeError(msg)
        return self.scenario


def create_draft_body() -> JsonObject:
    """Return the deterministic Alice/web draft-create body."""
    return {"category": "other", "origin": "web", "client_capabilities": ["repeatable_text"]}


def change_draft_body(
    *,
    base_revision: int,
    idempotency_key: str,
    operation_id: UUID = DISPLAY_NAME_OPERATION_ID,
    value: str = "Alice fuzz draft",
) -> JsonObject:
    """Return a deterministic optimistic display-name change body."""
    return {
        "base_revision": base_revision,
        "client_instance_id": DEFAULT_CLIENT_INSTANCE_ID,
        "idempotency_key": idempotency_key,
        "operations": [
            {
                "operation_id": str(operation_id),
                "field_id": "display_name",
                "kind": "set",
                "value": value,
            }
        ],
    }


def create_draft_request(auth: DraftWebAuth) -> DraftRequest:
    """Return the deterministic draft-create request."""
    return DraftRequest(
        operation_id="submission_draft_create",
        method="POST",
        path="/v1/submissions/drafts",
        json=create_draft_body(),
        headers=auth.write_headers,
        cookies=auth.cookies,
    )


def get_draft_request(auth: DraftWebAuth, draft_id: str) -> DraftRequest:
    """Return a draft-read request."""
    return DraftRequest(
        operation_id="submission_draft_get",
        method="GET",
        path=f"/v1/submissions/drafts/{draft_id}",
        cookies=auth.cookies,
    )


def change_draft_request(
    auth: DraftWebAuth,
    draft_id: str,
    base_revision: int,
    idempotency_key: str,
    *,
    operation_id: UUID = DISPLAY_NAME_OPERATION_ID,
    value: str = "Alice fuzz draft",
) -> DraftRequest:
    """Return a draft-change request."""
    return DraftRequest(
        operation_id="submission_draft_change",
        method="POST",
        path=f"/v1/submissions/drafts/{draft_id}/changes",
        json=change_draft_body(
            base_revision=base_revision,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            value=value,
        ),
        headers=auth.write_headers,
        cookies=auth.cookies,
    )


def submit_draft_request(auth: DraftWebAuth, draft_id: str) -> DraftRequest:
    """Return a draft-finalization request."""
    return DraftRequest(
        operation_id="submission_finalization_start",
        method="POST",
        path=f"/v1/submissions/drafts/{draft_id}/submission",
        headers=auth.write_headers,
        cookies=auth.cookies,
    )


def get_finalization_request(auth: DraftWebAuth, draft_id: str) -> DraftRequest:
    """Return a draft-finalization status request."""
    return DraftRequest(
        operation_id="submission_finalization_get",
        method="GET",
        path=f"/v1/submissions/drafts/{draft_id}/submission",
        cookies=auth.cookies,
    )


def delete_draft_request(auth: DraftWebAuth, draft_id: str) -> DraftRequest:
    """Return a draft-delete request."""
    return DraftRequest(
        operation_id="submission_draft_delete",
        method="DELETE",
        path=f"/v1/submissions/drafts/{draft_id}",
        headers=auth.write_headers,
        cookies=auth.cookies,
    )


def _expect_json(response: DraftResponse, status_code: int) -> JsonObject:
    if response.status_code != status_code or response.json is None:
        msg = f"Expected HTTP {status_code} JSON response, got {response.status_code}."
        raise AssertionError(msg)
    return response.json


def _expect_field(body: Mapping[str, object], key: str, expected: object) -> None:
    if body.get(key) != expected:
        msg = f"Expected response field {key!r} to be {expected!r}, got {body.get(key)!r}."
        raise AssertionError(msg)


def _expect_str(body: Mapping[str, object], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Expected response field {key!r} to be a non-empty string."
        raise AssertionError(msg)
    return value


def _expect_object(body: Mapping[str, object], key: str) -> JsonObject:
    value = body.get(key)
    if not isinstance(value, dict):
        msg = f"Expected response field {key!r} to be an object."
        raise TypeError(msg)
    return dict(value)
