"""Attachment membership and primary selection use the draft's optimistic revision."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditValues, PreparedEdit, Revision, require_revision
from squid.media.domain import MediaLimits
from squid.submissions.application.drafts import DraftUnitOfWork, StoredDraft, SubmissionDraftService

DRAFT_ATTACHMENTS = "draft.attachments"


class FileMembership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    present: bool
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = Field(default=None, pattern=r"^(schematic|image|video|unknown)$")


class AttachmentValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: tuple[FileMembership, ...] = ()
    primary: UUID | None = None
    select_primary: bool = False

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())


class AttachmentEditStore(Protocol):
    async def lock(self, draft_id: UUID) -> None: ...

    async def stage(self, draft: StoredDraft, files: tuple[FileMembership, ...]) -> None: ...

    async def read(self, draft: StoredDraft, files: tuple[FileMembership, ...]) -> AttachmentValues: ...

    async def validate(self, draft: StoredDraft, values: AttachmentValues, limits: MediaLimits) -> AttachmentValues: ...

    async def write(self, draft: StoredDraft, values: AttachmentValues) -> int: ...


class AttachmentEditUnitOfWork(DraftUnitOfWork, EditUnitOfWork, Protocol):
    @property
    def attachment_edits(self) -> AttachmentEditStore: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAttachments(PreparedEdit):
    draft: StoredDraft
    values: AttachmentValues


class DraftAttachmentsEdit:
    def __init__(self, drafts: SubmissionDraftService, limits: MediaLimits | None = None) -> None:
        self._drafts = drafts
        self._limits = limits or MediaLimits()

    async def prepare(self, uow: AttachmentEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedAttachments:
        if len(command.expected) != 1 or command.expected[0].target.kind != "draft":
            raise ValidationError
        expected = command.expected[0]
        await uow.attachment_edits.lock(UUID(expected.target.key))
        draft = await self._drafts.get_accessible_in(uow, UUID(expected.target.key), actor)
        values = AttachmentValues.model_validate_json(command.values.encoded)
        sources = tuple(item.source_id for item in values.files)
        if len(set(sources)) != len(sources):
            raise ValidationError
        # The repository locks the draft before inspecting processor references.
        before = await uow.attachment_edits.read(draft, values.files)
        revision = require_revision(expected, draft.snapshot.revision)
        files = tuple(
            item.model_copy(update={"present": requested.present})
            for item, requested in zip(before.files, values.files, strict=True)
        )
        desired = values.model_copy(
            update={
                "files": files,
                "primary": values.primary if values.select_primary else before.primary,
                "select_primary": True,
            }
        )
        after = await uow.attachment_edits.validate(draft, desired, self._limits)
        return PreparedAttachments(
            before.edit_values(),
            after.edit_values(),
            draft=draft,
            values=after,
            revisions=(revision,),
        )

    async def apply(
        self, uow: AttachmentEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedAttachments)
        revision = await uow.attachment_edits.write(prepared.draft, prepared.values)
        return (Revision(command.expected[0].target, revision),)
