"""Stage upload bytes before committing an atomic attachment addition or replacement."""

from uuid import UUID

from squid.core.actors import ActingContext
from squid.core.edits.domain import Revision, RevisionConflictError
from squid.core.submissions.application.attachment_edits import AttachmentEditUnitOfWork, FileMembership
from squid.core.submissions.application.drafts import SubmissionDraftService
from squid.core.submissions.application.edits import draft_target
from squid.core.unit_of_work import UnitOfWorkFactory


class DraftAttachmentUploads:
    """Stage files hidden from the draft until a versioned membership command accepts them.

    Staged files are recorded as removed attachments, so cleanup keeps them while the
    draft can still be edited (see `draft_keeps_file`).
    """

    def __init__(self, drafts: SubmissionDraftService, factory: UnitOfWorkFactory[AttachmentEditUnitOfWork]) -> None:
        self._drafts = drafts
        self._factory = factory

    async def stage(
        self, draft_id: UUID, revision: int, files: tuple[FileMembership, ...], *, context: ActingContext
    ) -> None:
        actor = context.effective
        with context.scope():
            async with self._factory() as uow:
                draft = await self._drafts.get_accessible_in(uow, draft_id, actor)
                if draft.snapshot.revision != revision:
                    raise RevisionConflictError(Revision(draft_target(draft_id), revision), draft.snapshot.revision)
                await uow.attachment_edits.stage(draft, files)
                await uow.commit()
