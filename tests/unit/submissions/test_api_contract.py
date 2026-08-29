"""Isolated tests for the renderer-neutral submission HTTP contract."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.accounts.domain import IdentityProvider
from squid.accounts.errors import ConsentRequiredError
from squid.api.security import ANONYMOUS, Principal, current_principal
from squid.api.v1.schemas.submissions import (
    DraftChangeRequest,
    DraftCreateRequest,
    FormManifestResponse,
    SubmissionFinalizationResponse,
)
from squid.api.v1.submissions import (
    AuthenticatedSubmissionActor,
    SubmissionFormRevisionNotFoundError,
    authenticated_account,
    authenticated_submission_actor,
    get_submission_drafts,
    get_submission_finalization,
    get_submission_forms,
    router,
)
from squid.core.errors import AuthenticationError
from squid.submissions.application import (
    AppliedDraftChange,
    FinalizationJobSnapshot,
    FormOptionSet,
    StoredDraft,
    build_submission_manifest,
)
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftSnapshot,
    FinalizationJobStatus,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionOrigin,
    SubmissionTargetResult,
)

ACCOUNT_ID = 42
JAVA_UUID = UUID("00000000-0000-4000-8000-000000000051")
INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000052")
GRANT_ID = UUID("00000000-0000-4000-8000-000000000053")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")


def stored_draft(
    *,
    owner_account_id: int = ACCOUNT_ID,
    category: str = "door",
    origin: SubmissionOrigin = SubmissionOrigin.WEB,
) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=UUID("64760b2f-b352-45e0-9ed1-67b9da901992"),
            owner_account_id=owner_account_id,
            schema_id="build_submission.v1",
            schema_revision=1,
            category=category,
        ),
        origin=origin,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )


class FakeForms:
    def __init__(self) -> None:
        self.manifest_locales: list[str | None] = []
        self.revision_calls: list[tuple[str, int, str | None]] = []
        self.option_calls: list[tuple[str, str, str | None]] = []

    def manifest(self, *, locale: str | None):
        self.manifest_locales.append(locale)
        return build_submission_manifest(locale)

    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None):
        self.revision_calls.append((schema_id, revision, locale))
        if schema_id == "build_submission.v1" and revision == 1:
            return build_submission_manifest(locale)
        return None

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        self.option_calls.append((source, category, locale))
        return FormOptionSet(source, category, 8, (ChoiceOption("slim", "Slim"),))


class FakeDrafts:
    def __init__(self) -> None:
        self.current = stored_draft()
        self.created_with: tuple[int, str, SubmissionOrigin, frozenset[str], str | None, UUID | None] | None = None
        self.change_seen: DraftChange | None = None
        self.deleted: tuple[UUID, int] | None = None

    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        source_installation_id: UUID | None = None,
    ) -> StoredDraft:
        self.created_with = (
            owner_account_id,
            category,
            origin,
            client_capabilities,
            locale,
            source_installation_id,
        )
        self.current = stored_draft(
            owner_account_id=owner_account_id,
            category=category,
            origin=origin,
        )
        return self.current

    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        assert account_id == ACCOUNT_ID
        assert limit == 10
        return (self.current,)

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        assert draft_id == self.current.snapshot.id
        assert account_id == ACCOUNT_ID
        return self.current

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        locale: str | None,
    ) -> AppliedDraftChange:
        assert draft_id == self.current.snapshot.id
        assert account_id == ACCOUNT_ID
        assert locale == "en"
        self.change_seen = change
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))
        return AppliedDraftChange(self.current)

    async def delete(self, draft_id: UUID, account_id: int) -> None:
        self.deleted = (draft_id, account_id)


class FakeFinalization:
    def __init__(self, draft_id: UUID) -> None:
        self.snapshot = FinalizationJobSnapshot(
            job_id=UUID("3ff2c7e7-8df7-4147-853f-fea71a8c39e4"),
            draft_id=draft_id,
            draft_revision=1,
            status=FinalizationJobStatus.NEEDS_ATTENTION,
            attempts=0,
            available_at=NOW,
            attention_at=NOW,
            issues=(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING),),
        )
        self.submit_calls: list[tuple[UUID, int, str | None]] = []
        self.status_calls: list[tuple[UUID, int]] = []

    async def submit(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        locale: str | None,
    ) -> FinalizationJobSnapshot:
        self.submit_calls.append((draft_id, account_id, locale))
        return self.snapshot

    async def status(self, draft_id: UUID, account_id: int) -> FinalizationJobSnapshot | None:
        self.status_calls.append((draft_id, account_id))
        return self.snapshot


def test_manifest_dto_is_stable_strict_and_json_safe() -> None:
    response = FormManifestResponse.from_domain(build_submission_manifest())
    payload = response.model_dump(mode="json")

    assert payload["schema_id"] == "build_submission.v1"
    assert payload["common_sections"][0]["fields"][0]["control"] == "text"
    assert payload["common_sections"][0]["fields"][0]["origins"] == ["cli", "discord", "fabric", "paper", "web"]
    assert "type_label" not in {field["id"] for section in payload["common_sections"] for field in section["fields"]}

    with pytest.raises(PydanticValidationError):
        DraftCreateRequest.model_validate(
            {"category": "door", "origin": "web", "client_capabilities": [], "unknown": True}
        )


def test_draft_change_rejects_non_json_and_client_schematic_assertions() -> None:
    base = {
        "base_revision": 0,
        "client_instance_id": "browser:a",
        "idempotency_key": "change-key-0001",
        "operations": [
            {
                "operation_id": str(uuid4()),
                "field_id": "display_name",
                "kind": "set",
                "value": "Compact door",
            }
        ],
    }

    with pytest.raises(PydanticValidationError):
        DraftChangeRequest.model_validate({**base, "has_sanitized_schematic": True})

    with pytest.raises(PydanticValidationError):
        DraftChangeRequest.model_validate(
            {
                "base_revision": 0,
                "client_instance_id": "browser:a",
                "idempotency_key": "change-key-0001",
                "operations": [
                    {
                        "operation_id": str(uuid4()),
                        "field_id": "display_name",
                        "kind": "set",
                        "value": uuid4(),
                    }
                ],
            }
        )


async def test_submission_routes_map_forms_and_owned_draft_operations() -> None:
    app = FastAPI()
    app.include_router(router)
    forms = FakeForms()
    drafts = FakeDrafts()
    finalization = FakeFinalization(drafts.current.snapshot.id)

    async def form_dependency() -> FakeForms:
        return forms

    async def draft_dependency() -> FakeDrafts:
        return drafts

    async def finalization_dependency() -> FakeFinalization:
        return finalization

    async def account_dependency() -> int:
        return ACCOUNT_ID

    async def actor_dependency() -> AuthenticatedSubmissionActor:
        return AuthenticatedSubmissionActor(ACCOUNT_ID, SubmissionOrigin.WEB)

    async def principal_dependency() -> Principal:
        return Principal(kind="account", subject=f"account:{ACCOUNT_ID}", account_id=ACCOUNT_ID)

    app.dependency_overrides[get_submission_forms] = form_dependency
    app.dependency_overrides[get_submission_drafts] = draft_dependency
    app.dependency_overrides[get_submission_finalization] = finalization_dependency
    app.dependency_overrides[authenticated_account] = account_dependency
    app.dependency_overrides[authenticated_submission_actor] = actor_dependency
    app.dependency_overrides[current_principal] = principal_dependency

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        manifest_response = await client.get("/submissions/form/current", headers={"Accept-Language": "zh-TW"})
        pinned_manifest_response = await client.get(
            "/submissions/form/schemas/build_submission.v1/revisions/1",
            headers={"Accept-Language": "zh-TW"},
        )
        with pytest.raises(SubmissionFormRevisionNotFoundError):
            await client.get("/submissions/form/schemas/build_submission.v1/revisions/999")
        options_response = await client.get(
            "/submissions/form/options/approved_restrictions",
            params={"category": "door"},
        )
        create_response = await client.post(
            "/submissions/drafts",
            json={
                "category": "door",
                "origin": "web",
                "client_capabilities": ["repeatable_text"],
            },
        )
        draft_id = create_response.json()["id"]
        change_response = await client.post(
            f"/submissions/drafts/{draft_id}/changes",
            json={
                "base_revision": 0,
                "client_instance_id": "paper:player-session",
                "idempotency_key": "change-key-0001",
                "operations": [
                    {
                        "operation_id": str(uuid4()),
                        "field_id": "display_name",
                        "kind": "set",
                        "value": "Compact door",
                    }
                ],
            },
        )
        list_response = await client.get("/submissions/drafts")
        get_response = await client.get(f"/submissions/drafts/{draft_id}")
        submit_response = await client.post(f"/submissions/drafts/{draft_id}/submission")
        submission_response = await client.get(f"/submissions/drafts/{draft_id}/submission")
        delete_response = await client.delete(f"/submissions/drafts/{draft_id}")

    assert manifest_response.status_code == 200
    assert manifest_response.json()["schema_id"] == "build_submission.v1"
    assert forms.manifest_locales == ["zh-CN"]
    assert pinned_manifest_response.status_code == 200
    assert pinned_manifest_response.json()["revision"] == 1
    assert forms.revision_calls == [
        ("build_submission.v1", 1, "zh-CN"),
        ("build_submission.v1", 999, "en"),
    ]
    assert options_response.status_code == 200
    assert options_response.json()["options"] == [{"value": "slim", "label": "Slim"}]
    assert forms.option_calls == [("approved_restrictions", "door", "en")]
    assert create_response.status_code == 201
    assert create_response.json()["origin"] == "web"
    assert drafts.created_with == (
        ACCOUNT_ID,
        "door",
        SubmissionOrigin.WEB,
        frozenset({"repeatable_text"}),
        "en",
        None,
    )
    assert change_response.status_code == 200
    assert change_response.json()["draft"]["revision"] == 1
    assert change_response.json()["replayed"] is False
    assert drafts.change_seen is not None
    assert drafts.change_seen.operations[0].value == "Compact door"
    assert list_response.status_code == 200
    assert list_response.json() == {
        "drafts": [
            {
                "id": draft_id,
                "schema_id": "build_submission.v1",
                "schema_revision": 1,
                "category": "door",
                "revision": 1,
                "status": "editing",
                "origin": "web",
                "display_name": "Compact door",
                "created_at": "2026-08-11T12:00:00Z",
                "updated_at": "2026-08-11T12:00:00Z",
                "expires_at": "2026-08-18T12:00:00Z",
            }
        ]
    }
    assert get_response.json()["answers"] == {"display_name": "Compact door"}
    assert submit_response.status_code == 202
    assert submit_response.json() == {
        "draft_id": draft_id,
        "draft_revision": 1,
        "status": "needs_attention",
        "issues": [{"field_id": "schematic", "reason": "schematic_processing"}],
        "build_id": None,
    }
    assert submission_response.json() == submit_response.json()
    assert finalization.submit_calls == [(UUID(draft_id), ACCOUNT_ID, "en")]
    assert finalization.status_calls == [(UUID(draft_id), ACCOUNT_ID)]
    assert delete_response.status_code == 204
    assert drafts.deleted == (UUID(draft_id), ACCOUNT_ID)


async def test_draft_authentication_requires_a_human_account() -> None:
    with pytest.raises(AuthenticationError):
        await authenticated_account(ANONYMOUS)


async def test_draft_authentication_requires_current_privacy_consent() -> None:
    principal = Principal(
        kind="account",
        subject="account:42",
        account_id=42,
        discord_id=123,
        consent_pending=True,
    )

    with pytest.raises(ConsentRequiredError) as error:
        await authenticated_account(principal)

    assert error.value.context == {
        "account_id": 42,
        "provider": IdentityProvider.DISCORD,
        "subject": "123",
    }


async def test_player_grant_derives_minecraft_origin() -> None:
    principal = Principal(
        kind="minecraft_player",
        subject="minecraft-grant:test",
        account_id=42,
        minecraft_origin="paper",
        java_uuid=JAVA_UUID,
        installation_id=INSTALLATION_ID,
        grant_id=GRANT_ID,
    )

    actor = await authenticated_submission_actor(principal)

    assert actor == AuthenticatedSubmissionActor(
        42,
        SubmissionOrigin.PAPER,
        java_uuid=JAVA_UUID,
        installation_id=INSTALLATION_ID,
        grant_id=GRANT_ID,
    )


async def test_cli_session_derives_distinct_submission_origin() -> None:
    device_id = UUID("ea252a1c-0bcd-47f7-84d8-36e6801eb374")
    session_id = UUID("f5f51999-37c1-4a85-9d7e-f53875428f99")
    principal = Principal(
        kind="cli",
        subject=f"cli-session:{session_id}",
        account_id=42,
        cli_device_id=device_id,
        cli_session_id=session_id,
    )

    actor = await authenticated_submission_actor(principal)

    assert actor == AuthenticatedSubmissionActor(42, SubmissionOrigin.CLI)


def test_finalization_response_does_not_expose_worker_or_target_internals() -> None:
    snapshot = FinalizationJobSnapshot(
        job_id=UUID("3ff2c7e7-8df7-4147-853f-fea71a8c39e4"),
        draft_id=stored_draft().snapshot.id,
        draft_revision=2,
        status=FinalizationJobStatus.COMPLETED,
        attempts=2,
        available_at=NOW,
        claimed_at=None,
        claim_token=None,
        completed_at=NOW,
        last_error="private worker detail",
        result=SubmissionTargetResult(91, "postgres_builds", {"private": "value"}),
    )

    payload = SubmissionFinalizationResponse.from_domain(snapshot).model_dump(mode="json")

    assert payload["build_id"] == 91
    assert "job_id" not in payload
    assert "attempts" not in payload
    assert "last_error" not in payload
    assert "target_key" not in payload
    assert "provenance" not in payload
