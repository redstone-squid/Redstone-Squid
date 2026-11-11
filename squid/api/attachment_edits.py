"""Bind attachment HTTP mutations to the draft revision and recoverable request identity."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Response

from squid.api.idempotency import edit_operation_id
from squid.api.revisions import expected_revision, revision_etag
from squid.api.v1.submissions import AccountId, get_submission_edits
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import EditCommand, Revision
from squid.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, AttachmentValues
from squid.submissions.application.edits import draft_target


@dataclass(frozen=True, slots=True)
class AttachmentEditRequest:
    runner: EditRunner
    actor: Actor
    revision: Revision
    operation_id: UUID

    async def save(self, values: AttachmentValues, response: Response) -> None:
        result = await self.runner.execute(
            EditCommand(DRAFT_ATTACHMENTS, (self.revision,), values.edit_values(), operation_id=self.operation_id),
            self.actor,
        )
        response.headers["ETag"] = revision_etag(result.outcome.revisions[0])


def attachment_edit_request(
    draft_id: UUID,
    account_id: AccountId,
    runner: Annotated[EditRunner, Depends(get_submission_edits)],
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AttachmentEditRequest:
    return AttachmentEditRequest(
        runner, Actor(account_id=account_id), expected_revision(draft_target(draft_id), if_match), operation_id
    )


AttachmentEdit = Annotated[AttachmentEditRequest, Depends(attachment_edit_request)]
