"""Isolated tests for the renderer-neutral submission HTTP contract."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.security import ANONYMOUS
from squid.api.v1.schemas.submissions import DraftChangeRequest, DraftCreateRequest, FormManifestResponse
from squid.api.v1.submissions import (
    authenticated_account,
    get_submission_drafts,
    get_submission_forms,
    router,
)
from squid.core.errors import AuthenticationError
from squid.submissions.application import AppliedDraftChange, FormOptionSet, StoredDraft, build_submission_manifest
from squid.submissions.domain import ChoiceOption, DraftChange, DraftSnapshot, SubmissionOrigin

ACCOUNT_ID = 42
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
        self.option_calls: list[tuple[str, str, str | None]] = []

    def manifest(self, *, locale: str | None):
        self.manifest_locales.append(locale)
        return build_submission_manifest(locale)

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        self.option_calls.append((source, category, locale))
        return FormOptionSet(source, category, 8, (ChoiceOption("slim", "Slim"),))


class FakeDrafts:
    def __init__(self) -> None:
        self.current = stored_draft()
        self.created_with: tuple[int, str, SubmissionOrigin, frozenset[str], str | None] | None = None
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
    ) -> StoredDraft:
        self.created_with = (owner_account_id, category, origin, client_capabilities, locale)
        self.current = stored_draft(
            owner_account_id=owner_account_id,
            category=category,
            origin=origin,
        )
        return self.current

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


def test_manifest_dto_is_stable_strict_and_json_safe() -> None:
    response = FormManifestResponse.from_domain(build_submission_manifest())
    payload = response.model_dump(mode="json")

    assert payload["schema_id"] == "build_submission.v1"
    assert payload["common_sections"][0]["fields"][0]["control"] == "text"
    assert payload["common_sections"][0]["fields"][0]["origins"] == ["discord", "fabric", "paper", "web"]
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

    async def form_dependency() -> FakeForms:
        return forms

    async def draft_dependency() -> FakeDrafts:
        return drafts

    async def account_dependency() -> int:
        return ACCOUNT_ID

    app.dependency_overrides[get_submission_forms] = form_dependency
    app.dependency_overrides[get_submission_drafts] = draft_dependency
    app.dependency_overrides[authenticated_account] = account_dependency

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        manifest_response = await client.get("/submissions/form/current", headers={"Accept-Language": "zh-TW"})
        options_response = await client.get(
            "/submissions/form/options/approved_restrictions",
            params={"category": "door"},
        )
        create_response = await client.post(
            "/submissions/drafts",
            json={
                "category": "door",
                "origin": "paper",
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
        get_response = await client.get(f"/submissions/drafts/{draft_id}")
        delete_response = await client.delete(f"/submissions/drafts/{draft_id}")

    assert manifest_response.status_code == 200
    assert manifest_response.json()["schema_id"] == "build_submission.v1"
    assert forms.manifest_locales == ["zh-CN"]
    assert options_response.status_code == 200
    assert options_response.json()["options"] == [{"value": "slim", "label": "Slim"}]
    assert forms.option_calls == [("approved_restrictions", "door", "en")]
    assert create_response.status_code == 201
    assert create_response.json()["origin"] == "paper"
    assert drafts.created_with == (ACCOUNT_ID, "door", SubmissionOrigin.PAPER, frozenset({"repeatable_text"}), "en")
    assert change_response.status_code == 200
    assert change_response.json()["draft"]["revision"] == 1
    assert change_response.json()["replayed"] is False
    assert drafts.change_seen is not None
    assert drafts.change_seen.operations[0].value == "Compact door"
    assert get_response.json()["answers"] == {"display_name": "Compact door"}
    assert delete_response.status_code == 204
    assert drafts.deleted == (UUID(draft_id), ACCOUNT_ID)


async def test_draft_authentication_requires_a_human_account() -> None:
    with pytest.raises(AuthenticationError):
        await authenticated_account(ANONYMOUS)
