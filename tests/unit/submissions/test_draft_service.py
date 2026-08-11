from dataclasses import replace
from uuid import UUID

import pytest
from whenever import Instant

from squid.core.errors import JSONValue
from squid.submissions.application import (
    AppliedDraftChange,
    FixedAccountDraftCapacity,
    StoredDraft,
    SubmissionDraftService,
    build_submission_manifest,
)
from squid.submissions.domain import (
    DraftChange,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftCapacityExceededError,
    DraftIncompleteError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
)

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000101")
INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000103")
OPERATION_ID = UUID("00000000-0000-4000-8000-000000000102")
NOW = Instant.parse_iso("2026-08-11T00:00:00Z")


class FakeManifestRegistry:
    def __init__(self) -> None:
        self.manifest = build_submission_manifest("en")

    async def current(self, *, locale: str | None) -> FormManifest:
        assert locale == "en"
        return self.manifest

    async def get(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        assert locale == "en"
        if (schema_id, revision) == (self.manifest.schema_id, self.manifest.revision):
            return self.manifest
        return None


class FakeDraftRepository:
    def __init__(self) -> None:
        self.drafts: dict[UUID, StoredDraft] = {}
        self.replays: dict[tuple[UUID, str], AppliedDraftChange] = {}

    async def count_active_for_account(self, account_id: int) -> int:
        return sum(
            draft.snapshot.owner_account_id == account_id
            and draft.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
            for draft in self.drafts.values()
        )

    async def create(self, draft: StoredDraft) -> StoredDraft:
        self.drafts[draft.snapshot.id] = draft
        return draft

    async def get(self, draft_id: UUID) -> StoredDraft | None:
        return self.drafts.get(draft_id)

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: str,
    ) -> AppliedDraftChange | None:
        replay = self.replays.get((draft_id, idempotency_key))
        if replay is not None and replay.draft.snapshot.owner_account_id != account_id:
            raise DraftAccessDeniedError
        return replay

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftChange:
        current = self.drafts[draft_id]
        if current.snapshot.owner_account_id != account_id:
            raise DraftAccessDeniedError
        updated = replace(
            current,
            snapshot=current.snapshot.apply(change),
            updated_at=updated_at,
            expires_at=expires_at,
        )
        self.drafts[draft_id] = updated
        result = AppliedDraftChange(updated)
        self.replays[(draft_id, change.idempotency_key)] = AppliedDraftChange(updated, replayed=True)
        return result

    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        updated_at: Instant,
        expires_at: Instant,
    ) -> StoredDraft:
        current = self.drafts[draft_id]
        assert current.snapshot.owner_account_id == account_id
        assert current.snapshot.revision == expected_revision
        updated = replace(
            current,
            snapshot=current.snapshot.transition(status),
            updated_at=updated_at,
            expires_at=expires_at,
        )
        self.drafts[draft_id] = updated
        return updated

    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool:
        current = self.drafts.get(draft_id)
        if current is None or current.snapshot.owner_account_id != account_id:
            return False
        del self.drafts[draft_id]
        return True

    async def expire_due(self, *, now: Instant, limit: int = 100) -> int:
        due = [
            draft_id
            for draft_id, draft in self.drafts.items()
            if draft.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
            and draft.expires_at <= now
        ][:limit]
        for draft_id in due:
            current = self.drafts[draft_id]
            self.drafts[draft_id] = replace(
                current,
                snapshot=replace(current.snapshot, status=DraftStatus.EXPIRED),
                updated_at=now,
            )
        return len(due)


def _change(base_revision: int = 0) -> DraftChange:
    return DraftChange(
        base_revision=base_revision,
        client_instance_id="fabric:device-1",
        idempotency_key="operation-0001",
        operations=(FieldOperation(OPERATION_ID, "display_name", FieldOperationKind.SET, "My build"),),
    )


def _complete_answers() -> dict[str, JSONValue]:
    return {
        "capture_width": 3,
        "capture_height": 4,
        "capture_depth": 5,
        "source_version": "26.1.2",
        "creators": ["Builder"],
        "schematic_visibility": "reviewer_only",
    }


@pytest.mark.asyncio
async def test_create_pins_schema_and_enforces_renderer_capabilities() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry())

    with pytest.raises(DraftSchemaUnsupportedError):
        await service.create(
            owner_account_id=7,
            category="door",
            origin=SubmissionOrigin.FABRIC,
            client_capabilities=frozenset(),
            locale="en",
            now=NOW,
            draft_id=DRAFT_ID,
        )

    draft = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.FABRIC,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
    )

    assert draft.snapshot.schema_id == "build_submission.v1"
    assert draft.snapshot.schema_revision == 1
    assert draft.expires_at == NOW.add(days=7, days_assumed_24h_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "source_installation_id"),
    [
        (SubmissionOrigin.PAPER, None),
        (SubmissionOrigin.WEB, INSTALLATION_ID),
    ],
)
async def test_create_requires_server_derived_installation_provenance_only_for_paper(
    origin: SubmissionOrigin,
    source_installation_id: UUID | None,
) -> None:
    service = SubmissionDraftService(FakeDraftRepository(), FakeManifestRegistry())

    with pytest.raises(ValueError, match="Paper drafts require server-derived installation provenance"):
        await service.create(
            owner_account_id=7,
            category="other",
            origin=origin,
            client_capabilities=frozenset({"repeatable_text"}),
            locale="en",
            source_installation_id=source_installation_id,
            now=NOW,
            draft_id=DRAFT_ID,
        )


@pytest.mark.asyncio
async def test_capacity_and_single_owner_are_enforced() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry(), FixedAccountDraftCapacity(1))
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
    )

    with pytest.raises(DraftCapacityExceededError):
        await service.create(
            owner_account_id=7,
            category="other",
            origin=SubmissionOrigin.WEB,
            client_capabilities=frozenset({"repeatable_text"}),
            locale="en",
            now=NOW,
        )
    with pytest.raises(DraftAccessDeniedError):
        await service.get_owned(DRAFT_ID, 8)

    with pytest.raises(DraftAccessDeniedError):
        await service.delete(DRAFT_ID, 8)
    await service.delete(DRAFT_ID, 7)
    assert DRAFT_ID not in repository.drafts


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [DraftStatus.PROCESSING, DraftStatus.SUBMITTED, DraftStatus.EXPIRED])
async def test_delete_rejects_every_noneditable_lifecycle_state(status: DraftStatus) -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry())
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
    )
    repository.drafts[DRAFT_ID] = replace(created, snapshot=replace(created.snapshot, status=status))

    with pytest.raises(DraftStateConflictError) as exc_info:
        await service.delete(DRAFT_ID, 7)

    operation = "access" if status is DraftStatus.EXPIRED else "delete"
    assert exc_info.value.public_context == {"status": status.value, "operation": operation}
    assert DRAFT_ID in repository.drafts


@pytest.mark.asyncio
async def test_change_is_idempotent_even_after_revision_advances() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry())
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
    )

    first = await service.apply_change(DRAFT_ID, 7, _change(), locale="en", now=NOW)
    replay = await service.apply_change(DRAFT_ID, 7, _change(), locale="en", now=NOW)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.draft.snapshot.revision == 1


@pytest.mark.asyncio
async def test_expired_draft_is_inaccessible_even_to_replayed_mutations() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        repository,
        FakeManifestRegistry(),
        now=lambda: NOW.add(days=8, days_assumed_24h_ok=True),
    )
    expired = StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="other",
        ),
        origin=SubmissionOrigin.WEB,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )
    repository.drafts[DRAFT_ID] = expired
    repository.replays[(DRAFT_ID, "operation-0001")] = AppliedDraftChange(expired, replayed=True)

    with pytest.raises(DraftStateConflictError) as access_error:
        await service.get_owned(DRAFT_ID, 7)
    with pytest.raises(DraftStateConflictError):
        await service.apply_change(DRAFT_ID, 7, _change(), locale="en")
    with pytest.raises(DraftStateConflictError):
        await service.validate_for_finalization(DRAFT_ID, 7, locale="en")
    with pytest.raises(DraftStateConflictError):
        await service.delete(DRAFT_ID, 7)

    assert access_error.value.public_context == {"status": "expired", "operation": "access"}


@pytest.mark.asyncio
async def test_expiry_batch_uses_the_service_clock_and_a_bounded_limit() -> None:
    repository = FakeDraftRepository()
    expiry_time = NOW.add(days=8, days_assumed_24h_ok=True)
    service = SubmissionDraftService(repository, FakeManifestRegistry(), now=lambda: expiry_time)
    for index in range(2):
        draft_id = UUID(f"00000000-0000-4000-8000-{index + 201:012d}")
        repository.drafts[draft_id] = StoredDraft(
            snapshot=DraftSnapshot(
                id=draft_id,
                owner_account_id=7,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
            ),
            origin=SubmissionOrigin.WEB,
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        )

    assert await service.expire_due(limit=1) == 1
    assert sum(draft.snapshot.status is DraftStatus.EXPIRED for draft in repository.drafts.values()) == 1


@pytest.mark.asyncio
async def test_finalization_validation_uses_complete_pinned_answers() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry())
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.PAPER,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        source_installation_id=INSTALLATION_ID,
        now=NOW,
        draft_id=DRAFT_ID,
    )

    repository.drafts[DRAFT_ID] = replace(
        created,
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="other",
            answers=_complete_answers(),
        ),
    )
    validated = await service.validate_for_finalization(
        DRAFT_ID,
        7,
        locale="en",
    )

    assert validated.draft.snapshot.status is DraftStatus.EDITING
    assert validated.normalized_answers["ai_generated"] is False


@pytest.mark.asyncio
async def test_processing_reports_field_errors_for_incomplete_web_draft() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(repository, FakeManifestRegistry())
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
    )

    with pytest.raises(DraftIncompleteError) as error:
        await service.validate_for_finalization(
            DRAFT_ID,
            7,
            locale="en",
        )
    assert error.value.public_context["field_errors"]
