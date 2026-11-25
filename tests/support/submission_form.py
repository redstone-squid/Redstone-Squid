"""In-memory application services for exercising the Discord submission form."""

from dataclasses import replace
from typing import override
from uuid import UUID

from whenever import Instant

from squid.core.actors import Actor
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.messages.domain import MessageSnapshot
from squid.core.schematics.application.services import SchematicService
from squid.core.submissions.application import (
    AppliedDraftChange,
    FormOptionSet,
    StoredDraft,
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFormService,
    build_submission_manifest,
)
from squid.core.submissions.application.finalization import SubmissionRequestResult
from squid.core.submissions.application.forms import CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION
from squid.core.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.core.submissions.domain import DraftChange, DraftSnapshot, FormManifest, SubmissionOrigin
from squid.core.submissions.domain.build_edit import DraftEditSource
from squid.core.submissions.domain.source_files import SubmissionSourceFile


class Schematics(SchematicService):
    def __init__(self) -> None:
        pass

    @property
    @override
    def available(self) -> bool:
        return False


class Forms(SubmissionFormService):
    def __init__(self) -> None:
        pass

    @override
    async def manifest(self, *, locale: str | None) -> FormManifest:
        return build_submission_manifest()

    @override
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None:
        return build_submission_manifest(revision=revision)

    @override
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        return FormOptionSet(source, category, 1, ())


class Drafts(SubmissionDraftService):
    def __init__(self) -> None:
        self.current: StoredDraft | None = None

    @override
    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        source_installation_id: UUID | None = None,
        edit_source: DraftEditSource | None = None,
        now: Instant | None = None,
        draft_id: UUID | None = None,
        inferred: bool = False,
        door_expansion: DoorExpansion | None = None,
        source_messages: tuple[MessageSnapshot, ...] = (),
        source_files: tuple[SubmissionSourceFile, ...] = (),
        source_issues: tuple[str, ...] = (),
        inference_run_id: UUID | None = None,
    ) -> StoredDraft:
        assert draft_id is not None
        created = Instant.now()
        self.current = StoredDraft(
            snapshot=DraftSnapshot(
                draft_id,
                owner_account_id,
                CURRENT_SUBMISSION_SCHEMA,
                CURRENT_SUBMISSION_SCHEMA_REVISION,
                category,
            ),
            origin=origin,
            created_at=created,
            updated_at=created,
            expires_at=created.add(days=7, days_assumed_24h_ok=True),
        )
        return self.current

    @override
    async def apply_change(
        self,
        draft_id: UUID,
        actor: Actor,
        change: DraftChange,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        assert self.current is not None
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))
        return AppliedDraftChange(self.current)

    @override
    async def get_accessible(self, draft_id: UUID, actor: Actor) -> StoredDraft:
        assert self.current is not None
        return self.current

    @override
    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        if self.current and self.current.snapshot.owner_account_id == account_id:
            return (self.current,)
        return ()


class Finalization(SubmissionFinalizationService):
    def __init__(self) -> None:
        pass

    @override
    async def status(self, draft_id: UUID, actor: Actor) -> SubmissionRequestResult | None:
        return None


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        pass

    @override
    async def list(self, draft_id: UUID, actor: Actor) -> tuple[SuppliedAttachment, ...]:
        return ()
