"""Bind attachment HTTP mutations to the draft revision and recoverable request identity."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from litestar.di import NamedDependency
from litestar.params import FromPath, HeaderParameter, SkipValidation

from squid.api.revisions import expected_revision, revision_etag
from squid.api.v1.submissions import AccountId
from squid.core.actors import Actor
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditCommand, LatestRevision, Revision
from squid.core.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, AttachmentValues
from squid.core.submissions.application.edits import draft_target


@dataclass(frozen=True, slots=True)
class AttachmentEditRequest:
    runner: EditRunner
    actor: Actor
    revision: Revision | LatestRevision
    operation_id: UUID

    async def save(self, values: AttachmentValues) -> str:
        result = await self.runner.execute(
            EditCommand(DRAFT_ATTACHMENTS, (self.revision,), values.edit_values(), operation_id=self.operation_id),
            self.actor,
        )
        return revision_etag(result.outcome.revisions[0])


def attachment_edit_request(
    draft_id: FromPath[UUID],
    account_id: AccountId,
    runner: NamedDependency[SkipValidation[EditRunner]],
    operation_id: NamedDependency[UUID],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
) -> AttachmentEditRequest:
    return AttachmentEditRequest(
        runner, Actor(account_id=account_id), expected_revision(draft_target(draft_id), if_match), operation_id
    )


AttachmentEdit = NamedDependency[SkipValidation[AttachmentEditRequest]]
