"""Bridges from finalization outcomes to the existing review and polling surfaces."""

import logging

from squid.submissions.application import SubmissionNotificationEvent, SubmissionReviewEvent

logger = logging.getLogger(__name__)


class PollableFinalizationStatusPublisher:
    """Record observability for status already retained in the finalization job.

    Draft owners read the durable state through the submission status endpoint. This
    adapter deliberately does not create notification consent or delivery state on
    their behalf.
    """

    async def publish(self, event: SubmissionNotificationEvent) -> None:
        logger.info(
            "Submission finalization status changed",
            extra={
                "squid.submission.event_id": str(event.event_id),
                "squid.submission.draft_id": str(event.draft_id),
                "squid.submission.status": event.status.value,
            },
        )


class ExistingBuildReviewPublisher:
    """Acknowledge review work already emitted by the build insert trigger.

    Creating the target build emits the durable ``build.submitted`` domain event.
    The normal worker materializes that event for staff, so publishing another event
    here would duplicate review notifications.
    """

    async def publish(self, event: SubmissionReviewEvent) -> None:
        logger.info(
            "Submission build entered the existing review pipeline",
            extra={
                "squid.submission.event_id": str(event.event_id),
                "squid.submission.draft_id": str(event.draft_id),
                "squid.build.id": event.build_id,
            },
        )
