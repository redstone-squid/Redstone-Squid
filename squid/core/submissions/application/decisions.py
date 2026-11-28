"""Staff decisions on submitted build revisions."""

from typing import Literal
from uuid import UUID

from squid.core.actors import ActingContext, application_operation
from squid.core.auth.application.viewer import ViewerQueries
from squid.core.builds.application.edits import BUILD_STATUS_EDIT, build_target
from squid.core.builds.domain import BuildStatus
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditCommand, EditValues, Revision
from squid.core.errors import AuthorizationError
from squid.core.submissions.application.reviews import SubmissionDetail


class SubmissionReviews:
    def __init__(self, views: ViewerQueries, edits: EditRunner) -> None:
        self._views = views
        self._edits = edits

    @application_operation
    async def decide(
        self,
        submission_id: UUID,
        revision: int,
        decision: Literal["approve", "reject"],
        feedback: str | None,
        operation_id: UUID,
        *,
        context: ActingContext,
    ) -> SubmissionDetail:
        detail = await self._views.submission(submission_id, context=context)
        if detail.submission.kind != "new_build":
            raise AuthorizationError
        status = BuildStatus.APPROVED if decision == "approve" else BuildStatus.REJECTED
        await self._edits.execute(
            EditCommand(
                BUILD_STATUS_EDIT,
                (Revision(build_target(detail.submission.build_id), revision),),
                EditValues.of({"submission_status": status.value, "feedback": feedback}),
                operation_id,
            ),
            context=context,
        )
        return await self._views.submission(submission_id, context=context)
