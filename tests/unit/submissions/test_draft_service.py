from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import cast
from uuid import UUID

import pytest
from whenever import Instant

from squid.core.actors import ActingContext, Actor, AnonymousActor, UserActor
from squid.core.errors import InvalidStateError, JSONValue
from squid.core.pagination import Page, PageSelector, offset_page
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.domain import Decision, PermissionCheck, Reason
from squid.core.permissions.errors import PermissionDeniedError, require_check
from squid.core.submissions.application import (
    AppliedDraftChange,
    AppliedDraftUpgrade,
    CheckedInFormManifestRegistry,
    FixedAccountDraftCapacity,
    StoredDraft,
    SubmissionDraftService,
)
from squid.core.submissions.application.drafts import MAX_DRAFT_EDITORS, DraftRole, DraftViewer
from squid.core.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    SubmissionOrigin,
)
from squid.core.submissions.domain.build_edit import DraftEditSource
from squid.core.submissions.errors import (
    DraftAccessDeniedError,
    DraftCapacityExceededError,
    DraftEditorLimitError,
    DraftNotShareableError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
    DraftValidationError,
)
from tests.support.draft_uow import DraftScope

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000101")
INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000103")
OPERATION_ID = UUID("00000000-0000-4000-8000-000000000102")
NOW = Instant.parse_iso("2026-08-11T00:00:00Z")
"""Shared fixture and service clock, keeping seven-day expiry independent of the calendar.

Tests of expiry deliberately move the service clock beyond this instant.
"""


class FakeDraftRepository:
    """Model ownership, revisions and replay in memory.

    Capacity counts active-status rows until expire_due marks them expired; no database
    locking or concurrent transaction behavior is modeled.
    """

    def __init__(self) -> None:
        self.drafts: dict[UUID, StoredDraft] = {}
        self.actors: list[int | None] = []
        self.replays: dict[tuple[UUID, str], AppliedDraftChange] = {}

    async def list_attention(self, *, selector: PageSelector, page_size: int, now: Instant) -> Page[StoredDraft]:
        return offset_page((), offset=selector.offset, page_size=page_size)

    async def list_active_for_account(
        self,
        account_id: int,
        *,
        now: Instant,
        limit: int,
    ) -> tuple[StoredDraft, ...]:
        return tuple(
            sorted(
                (
                    draft
                    for draft in self.drafts.values()
                    if account_id in draft.account_ids
                    and draft.snapshot.status
                    in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
                    and draft.expires_at > now
                ),
                key=lambda draft: (draft.updated_at, draft.snapshot.id),
                reverse=True,
            )[:limit]
        )

    async def create(self, draft: StoredDraft, *, capacity: int = 10) -> StoredDraft:
        if draft.snapshot.id in self.drafts:
            return self.drafts[draft.snapshot.id]
        count = sum(
            existing.inferred == draft.inferred
            and (draft.inferred or existing.snapshot.owner_account_id == draft.snapshot.owner_account_id)
            and existing.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
            for existing in self.drafts.values()
        )
        if count >= capacity:
            raise DraftCapacityExceededError(capacity)
        self.drafts[draft.snapshot.id] = draft
        return draft

    async def get(self, draft_id: UUID) -> StoredDraft | None:
        return self.drafts.get(draft_id)

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange | None:
        replay = self.replays.get((draft_id, change.idempotency_key))
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
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange:
        self.actors.append(actor_account_id)
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

    async def upgrade_manifest(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        target_schema_revision: int,
        answers: Mapping[str, JSONValue],
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftUpgrade:
        current = self.drafts[draft_id]
        assert current.snapshot.owner_account_id == account_id
        if current.snapshot.schema_revision == target_schema_revision:
            return AppliedDraftUpgrade(current, replayed=True)
        if current.snapshot.revision != expected_revision:
            raise DraftRevisionConflictError(expected=expected_revision, current=current.snapshot.revision)
        updated = replace(
            current,
            snapshot=replace(
                current.snapshot,
                schema_revision=target_schema_revision,
                revision=current.snapshot.revision + 1,
                answers=answers,
            ),
            updated_at=updated_at,
            expires_at=expires_at,
        )
        self.drafts[draft_id] = updated
        return AppliedDraftUpgrade(updated)

    async def add_editor(self, draft_id: UUID, account_id: int, *, limit: int) -> StoredDraft:
        current = self.drafts[draft_id]
        if account_id not in current.editor_account_ids and len(current.editor_account_ids) >= limit:
            raise DraftEditorLimitError(limit)
        self.drafts[draft_id] = replace(current, editor_account_ids=current.editor_account_ids | {account_id})
        return self.drafts[draft_id]

    async def remove_editor(self, draft_id: UUID, account_id: int) -> StoredDraft:
        current = self.drafts[draft_id]
        self.drafts[draft_id] = replace(current, editor_account_ids=current.editor_account_ids - {account_id})
        return self.drafts[draft_id]

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
        idempotency_key=DraftChangeKey("operation-0001"),
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
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )

    with pytest.raises(DraftSchemaUnsupportedError):
        await service.create(
            owner_account_id=7,
            category="door",
            origin=SubmissionOrigin.FABRIC,
            client_capabilities=frozenset(),
            locale="en",
            now=NOW,
            draft_id=DRAFT_ID,
            context=ActingContext.direct(UserActor(account_id=7)),
        )

    draft = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.FABRIC,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    assert draft.snapshot.schema_id == "build_submission.v1"
    assert draft.snapshot.schema_revision == 10
    assert draft.expires_at == NOW.add(days=7, days_assumed_24h_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "source_installation_id"),
    [
        (SubmissionOrigin.PAPER, None),
        (SubmissionOrigin.WEB, INSTALLATION_ID),
    ],
)
async def test_create_requires_server_derived_installation_origin_only_for_paper(
    origin: SubmissionOrigin,
    source_installation_id: UUID | None,
) -> None:
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, FakeDraftRepository())
    )

    with pytest.raises(ValueError, match="Paper drafts require server-derived installation identity"):
        await service.create(
            owner_account_id=7,
            category="other",
            origin=origin,
            client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
            locale="en",
            source_installation_id=source_installation_id,
            now=NOW,
            draft_id=DRAFT_ID,
            context=ActingContext.direct(UserActor(account_id=7)),
        )


@pytest.mark.asyncio
async def test_capacity_and_single_owner_are_enforced() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(),
        FixedAccountDraftCapacity(1),
        now=lambda: NOW,
        uow_factory=partial(DraftScope, repository),
    )
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    with pytest.raises(DraftCapacityExceededError):
        await service.create(
            owner_account_id=7,
            category="other",
            origin=SubmissionOrigin.WEB,
            client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
            locale="en",
            now=NOW,
            context=ActingContext.direct(UserActor(account_id=7)),
        )
    with pytest.raises(DraftAccessDeniedError):
        await service.get_owned(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=8)))

    with pytest.raises(DraftAccessDeniedError):
        await service.delete(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=8)))
    await service.delete(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=7)))
    assert DRAFT_ID not in repository.drafts


@pytest.mark.asyncio
async def test_active_draft_discovery_is_bounded_owned_and_unexpired() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    active = StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=2,
            category="other",
            answers={"display_name": "CLI build"},
        ),
        origin=SubmissionOrigin.CLI,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )
    repository.drafts[DRAFT_ID] = active
    repository.drafts[UUID("00000000-0000-4000-8000-000000000104")] = replace(
        active,
        snapshot=replace(
            active.snapshot,
            id=UUID("00000000-0000-4000-8000-000000000104"),
            owner_account_id=8,
        ),
    )
    repository.drafts[UUID("00000000-0000-4000-8000-000000000105")] = replace(
        active,
        snapshot=replace(
            active.snapshot,
            id=UUID("00000000-0000-4000-8000-000000000105"),
            status=DraftStatus.SUBMITTED,
        ),
    )
    repository.drafts[UUID("00000000-0000-4000-8000-000000000106")] = replace(
        active,
        snapshot=replace(active.snapshot, id=UUID("00000000-0000-4000-8000-000000000106")),
        expires_at=NOW,
    )

    discovered = await service.list_active(context=ActingContext.direct(UserActor(account_id=7)))

    assert discovered == (active,)
    with pytest.raises(InvalidStateError, match="between 1 and 10"):
        await service.list_active(limit=11, context=ActingContext.direct(UserActor(account_id=7)))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [DraftStatus.PROCESSING, DraftStatus.SUBMITTED, DraftStatus.EXPIRED])
async def test_delete_rejects_every_noneditable_lifecycle_state(status: DraftStatus) -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    repository.drafts[DRAFT_ID] = replace(created, snapshot=replace(created.snapshot, status=status))

    with pytest.raises(DraftStateConflictError) as exc_info:
        await service.delete(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=7)))

    operation = "access" if status is DraftStatus.EXPIRED else "delete"
    assert exc_info.value.public_context == {"status": status.value, "operation": operation}
    assert DRAFT_ID in repository.drafts


@pytest.mark.asyncio
async def test_change_is_idempotent_even_after_revision_advances() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    first = await service.apply_change(
        DRAFT_ID, _change(), context=ActingContext.for_actor(UserActor(account_id=7)), locale="en", now=NOW
    )
    replay = await service.apply_change(
        DRAFT_ID, _change(), context=ActingContext.for_actor(UserActor(account_id=7)), locale="en", now=NOW
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.draft.snapshot.revision == 1


@pytest.mark.asyncio
async def test_expired_draft_is_inaccessible_even_to_replayed_mutations() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(),
        now=lambda: NOW.add(days=8, days_assumed_24h_ok=True),
        uow_factory=partial(DraftScope, repository),
    )
    expired = StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=2,
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
        await service.get_owned(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=7)))
    with pytest.raises(DraftStateConflictError):
        await service.apply_change(
            DRAFT_ID, _change(), context=ActingContext.for_actor(UserActor(account_id=7)), locale="en"
        )
    with pytest.raises(DraftStateConflictError):
        await service.validate_for_finalization(
            DRAFT_ID, context=ActingContext.for_actor(UserActor(account_id=7)), locale="en"
        )
    with pytest.raises(DraftStateConflictError):
        await service.delete(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=7)))

    assert access_error.value.public_context == {"status": "expired", "operation": "access"}


@pytest.mark.asyncio
async def test_expiry_batch_uses_the_service_clock_and_a_bounded_limit() -> None:
    repository = FakeDraftRepository()
    expiry_time = NOW.add(days=8, days_assumed_24h_ok=True)
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: expiry_time, uow_factory=partial(DraftScope, repository)
    )
    for index in range(2):
        draft_id = UUID(f"00000000-0000-4000-8000-{index + 201:012d}")
        repository.drafts[draft_id] = StoredDraft(
            snapshot=DraftSnapshot(
                id=draft_id,
                owner_account_id=7,
                schema_id="build_submission.v1",
                schema_revision=2,
                category="other",
            ),
            origin=SubmissionOrigin.WEB,
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        )

    assert await service.expire_due(limit=1, context=ActingContext.worker("test-worker")) == 1
    assert sum(draft.snapshot.status is DraftStatus.EXPIRED for draft in repository.drafts.values()) == 1


@pytest.mark.asyncio
async def test_finalization_validation_uses_complete_pinned_answers() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.PAPER,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        source_installation_id=INSTALLATION_ID,
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    repository.drafts[DRAFT_ID] = replace(
        created,
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=2,
            category="other",
            answers=_complete_answers(),
        ),
    )
    validated = await service.validate_for_finalization(
        DRAFT_ID,
        context=ActingContext.for_actor(UserActor(account_id=7)),
        locale="en",
    )

    assert validated.draft.snapshot.status is DraftStatus.EDITING
    assert validated.normalized_answers["ai_generated"] is False


@pytest.mark.asyncio
async def test_processing_reports_field_errors_for_incomplete_web_draft() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    with pytest.raises(DraftValidationError) as error:
        await service.validate_for_finalization(
            DRAFT_ID,
            context=ActingContext.for_actor(UserActor(account_id=7)),
            locale="en",
        )
    assert error.value.field_issues


@pytest.mark.asyncio
async def test_manifest_upgrade_preserves_answers_and_is_idempotent_by_target_revision() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.PAPER,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        source_installation_id=INSTALLATION_ID,
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    repository.drafts[DRAFT_ID] = replace(
        created,
        snapshot=replace(
            created.snapshot,
            schema_revision=2,
            answers={
                "completion": "Built at spawn",
                "ai_generated": False,
                "sponsor_attribution": False,
            },
        ),
    )

    upgraded = await service.upgrade_manifest(
        DRAFT_ID,
        expected_revision=0,
        target_revision=3,
        locale="en",
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    replayed = await service.upgrade_manifest(
        DRAFT_ID,
        expected_revision=0,
        target_revision=3,
        locale="en",
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    assert upgraded.draft.snapshot.schema_revision == 3
    assert upgraded.draft.snapshot.revision == 1
    assert upgraded.draft.snapshot.answers == {
        "completion": "Built at spawn",
        "ai_generated": False,
        "sponsor_attribution": False,
    }
    assert not upgraded.replayed
    assert replayed.replayed
    assert replayed.draft == upgraded.draft


async def test_manifest_upgrade_to_revision_nine_turns_showcase_keys_into_tags() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    repository.drafts[DRAFT_ID] = replace(
        created,
        snapshot=replace(created.snapshot, schema_revision=8, answers={"showcase_tags": ["compact", "tileable"]}),
    )

    upgraded = await service.upgrade_manifest(
        DRAFT_ID,
        expected_revision=0,
        target_revision=9,
        locale="en",
        context=ActingContext.direct(UserActor(account_id=7)),
    )

    assert upgraded.draft.snapshot.answers == {"showcase_tags": [{"key": "compact"}, {"key": "tileable"}]}


@pytest.mark.asyncio
async def test_manifest_upgrade_failure_does_not_touch_the_draft() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    before = replace(
        created,
        snapshot=replace(created.snapshot, schema_revision=2, answers={"unknown_answer": True}),
    )
    repository.drafts[DRAFT_ID] = before

    with pytest.raises(DraftValidationError):
        await service.upgrade_manifest(
            DRAFT_ID,
            expected_revision=0,
            target_revision=3,
            locale="en",
            context=ActingContext.direct(UserActor(account_id=7)),
        )

    assert repository.drafts[DRAFT_ID] == before


@pytest.mark.asyncio
async def test_manifest_upgrade_rejects_stale_edit_without_touching_the_draft() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    before = replace(created, snapshot=replace(created.snapshot, schema_revision=2, revision=2))
    repository.drafts[DRAFT_ID] = before

    with pytest.raises(DraftRevisionConflictError):
        await service.upgrade_manifest(
            DRAFT_ID,
            expected_revision=1,
            target_revision=3,
            locale="en",
            context=ActingContext.direct(UserActor(account_id=7)),
        )

    assert repository.drafts[DRAFT_ID] == before


@pytest.mark.asyncio
async def test_manifest_upgrade_rejects_an_unretained_source_without_touching_the_draft() -> None:
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, repository)
    )
    created = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        now=NOW,
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    before = replace(created, snapshot=replace(created.snapshot, schema_revision=999))
    repository.drafts[DRAFT_ID] = before

    with pytest.raises(DraftSchemaUnsupportedError):
        await service.upgrade_manifest(
            DRAFT_ID,
            expected_revision=0,
            target_revision=3,
            locale="en",
            context=ActingContext.direct(UserActor(account_id=7)),
        )

    assert repository.drafts[DRAFT_ID] == before


async def test_staff_correction_preserves_owner_and_rechecks_revocation() -> None:
    from squid.core.permissions.domain import PermissionNode

    class Permissions:
        allowed = True

        async def require(
            self, subject: Actor, *nodes: PermissionNode | str, uow: PermissionUnitOfWork | None = None
        ) -> None:
            require_check(
                PermissionCheck(
                    tuple(
                        Decision(
                            str(node), allowed=subject.linked_account_id == 8 and self.allowed, reason=Reason.DEFAULT
                        )
                        for node in nodes
                    )
                )
            )

    permissions = Permissions()
    repository = FakeDraftRepository()
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(),
        now=lambda: NOW,
        permissions=permissions,
        uow_factory=partial(DraftScope, repository),
    )
    await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.DISCORD,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    corrected = await service.apply_change(
        DRAFT_ID, _change(), context=ActingContext.for_actor(UserActor(account_id=8)), locale="en"
    )
    assert corrected.draft.snapshot.owner_account_id == 7
    assert repository.actors == [8]
    with pytest.raises(DraftAccessDeniedError) as denied:
        await service.get_owned(DRAFT_ID, context=ActingContext.direct(UserActor(account_id=8)))
    assert denied.value.public_context == {"reason": "owner_required"}
    assert denied.value.public_detail() == "Only the account that owns this draft can perform this action."
    permissions.allowed = False
    with pytest.raises(PermissionDeniedError):
        await service.get_accessible(DRAFT_ID, context=ActingContext.for_actor(UserActor(account_id=8)))
    with pytest.raises(PermissionDeniedError):
        await service.apply_change(
            DRAFT_ID, _change(base_revision=1), context=ActingContext.for_actor(UserActor(account_id=8)), locale="en"
        )
    with pytest.raises(PermissionDeniedError):
        await service.attention_inbox(context=ActingContext.for_actor(UserActor(account_id=8)))
    assert repository.actors == [8]


async def test_draft_access_requires_actor_even_when_account_owns_the_draft() -> None:
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(), now=lambda: NOW, uow_factory=partial(DraftScope, FakeDraftRepository())
    )
    draft = await service.create(
        owner_account_id=7,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=7)),
    )
    assert await service.get_accessible(DRAFT_ID, context=ActingContext.for_actor(UserActor(account_id=7))) == draft
    # A dynamically typed caller must not restore the old implicit account-to-actor conversion.
    with pytest.raises(InvalidStateError, match="requires an Actor"):
        await service.get_accessible(DRAFT_ID, context=ActingContext.for_actor(cast(Actor, 7)))
    with pytest.raises(DraftAccessDeniedError) as missing_account:
        await service.get_accessible(DRAFT_ID, context=ActingContext.for_actor(AnonymousActor()))
    assert missing_account.value.public_context == {"reason": "missing_account"}
    assert missing_account.value.public_detail() == "No linked account was found for this request."

    with pytest.raises(DraftAccessDeniedError) as unconfigured:
        await service.get_accessible(
            DRAFT_ID, context=ActingContext.for_actor(UserActor(account_id=8, is_bot_owner=True))
        )
    assert unconfigured.value.public_context == {"reason": "permissions_unconfigured"}
    assert unconfigured.value.public_detail() == (
        "Staff access to submission drafts is not configured for this service. Contact the service operator."
    )


class _NoStaff:
    async def require(self, subject: Actor, *nodes: object, uow: PermissionUnitOfWork | None = None) -> None:
        require_check(
            PermissionCheck(tuple(Decision(str(node), allowed=False, reason=Reason.DEFAULT) for node in nodes))
        )


async def _service_with_draft(repository: FakeDraftRepository, *, owner: int = 7) -> SubmissionDraftService:
    service = SubmissionDraftService(
        CheckedInFormManifestRegistry(),
        now=lambda: NOW,
        permissions=_NoStaff(),
        uow_factory=partial(DraftScope, repository),
    )
    await service.create(
        owner_account_id=owner,
        category="other",
        origin=SubmissionOrigin.WEB,
        client_capabilities=frozenset({"repeatable_text", "typed_credits"}),
        locale="en",
        draft_id=DRAFT_ID,
        context=ActingContext.direct(UserActor(account_id=owner)),
    )
    return service


def _as(account_id: int) -> ActingContext:
    return ActingContext.direct(UserActor(account_id=account_id))


async def test_editors_change_answers_but_only_the_owner_submits_and_discards() -> None:
    repository = FakeDraftRepository()
    service = await _service_with_draft(repository)
    with pytest.raises(PermissionDeniedError):
        await service.get_accessible(DRAFT_ID, context=_as(8))

    shared = await service.add_editor(DRAFT_ID, 8, context=_as(7))

    assert shared.editor_account_ids == {8}
    assert shared.viewer(8) == DraftViewer(DraftRole.EDITOR, can_edit=True, can_discard=False)
    assert [draft.snapshot.id for draft in await service.list_active(context=_as(8))] == [DRAFT_ID]
    changed = await service.apply_change(DRAFT_ID, _change(), context=_as(8), locale="en")
    assert changed.draft.snapshot.owner_account_id == 7
    assert repository.actors == [8]
    with pytest.raises(DraftAccessDeniedError) as denied:
        await service.get_accessible(DRAFT_ID, context=_as(8), owner_only=True)
    assert denied.value.public_context == {"reason": "owner_required"}
    with pytest.raises(DraftAccessDeniedError):
        await service.delete(DRAFT_ID, context=_as(8))
    with pytest.raises(DraftAccessDeniedError):
        await service.add_editor(DRAFT_ID, 9, context=_as(8))


async def test_owner_manages_editors_and_editors_can_only_leave() -> None:
    repository = FakeDraftRepository()
    service = await _service_with_draft(repository)
    await service.add_editor(DRAFT_ID, 8, context=_as(7))
    await service.add_editor(DRAFT_ID, 9, context=_as(7))

    assert (await service.add_editor(DRAFT_ID, 7, context=_as(7))).editor_account_ids == {8, 9}
    with pytest.raises(DraftAccessDeniedError):
        await service.remove_editor(DRAFT_ID, 9, context=_as(8))
    assert (await service.remove_editor(DRAFT_ID, 8, context=_as(8))).editor_account_ids == {9}
    assert (await service.remove_editor(DRAFT_ID, 9, context=_as(7))).editor_account_ids == frozenset()
    with pytest.raises(PermissionDeniedError):
        await service.get_accessible(DRAFT_ID, context=_as(9))


async def test_sharing_is_limited_and_excludes_build_edit_drafts() -> None:
    repository = FakeDraftRepository()
    service = await _service_with_draft(repository)
    for account_id in range(100, 100 + MAX_DRAFT_EDITORS):
        await service.add_editor(DRAFT_ID, account_id, context=_as(7))
    with pytest.raises(DraftEditorLimitError):
        await service.add_editor(DRAFT_ID, 99, context=_as(7))

    repository.drafts[DRAFT_ID] = replace(
        repository.drafts[DRAFT_ID], edit_source=DraftEditSource(build_id=1, revision=1, answers={})
    )
    with pytest.raises(DraftNotShareableError):
        await service.add_editor(DRAFT_ID, 99, context=_as(7))
