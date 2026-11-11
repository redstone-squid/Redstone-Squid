"""Stage upload bytes before committing an atomic attachment addition or replacement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import anyio

from squid.core.actors import Actor
from squid.core.concurrency import task_group
from squid.edits.domain import Revision, RevisionConflictError
from squid.edits.retention import EditRetentionService
from squid.submissions.application.attachment_edits import AttachmentEditUnitOfWork, FileMembership
from squid.submissions.application.drafts import SubmissionDraftService
from squid.submissions.application.edits import draft_target
from squid.unit_of_work import UnitOfWorkFactory


class DraftAttachmentUploads:
    """Staged files stay hidden and leased until a versioned membership command accepts them."""

    def __init__(self, drafts: SubmissionDraftService, factory: UnitOfWorkFactory[AttachmentEditUnitOfWork]) -> None:
        self._drafts = drafts
        self._factory = factory
        self._retention = EditRetentionService(factory)

    @asynccontextmanager
    async def staging(
        self, draft_id: UUID, actor: Actor, revision: int, files: tuple[FileMembership, ...]
    ) -> AsyncIterator[None]:
        async with self._factory() as uow:
            draft = await self._drafts.get_accessible_in(uow, draft_id, actor)
            if draft.snapshot.revision != revision:
                raise RevisionConflictError(Revision(draft_target(draft_id), revision), draft.snapshot.revision)
            await uow.attachment_edits.stage(draft, files)
            for item in files:
                await uow.edits.retain(item.source_id, (f"attachment:{item.source_id}",))
            await uow.commit()
        retained = frozenset(item.source_id for item in files)

        async def renew() -> None:
            while True:
                await anyio.sleep(30)
                await self._retention.sync(retained, frozenset())

        try:
            async with task_group() as tasks:
                tasks.start_soon(renew)
                try:
                    yield
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            with anyio.move_on_after(10, shield=True):
                await self._retention.sync(frozenset(), retained)
