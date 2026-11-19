"""Persistence adapters for synchronized submission drafts."""

from squid.core.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.core.submissions.infrastructure.options import ApprovedSubmissionOptionCatalog
from squid.core.submissions.infrastructure.repository import PostgresDraftRepository
from squid.core.submissions.infrastructure.sponsors import PaperSponsorResolver
from squid.core.submissions.models.entities import (
    SubmissionDraft,
    SubmissionDraftAccess,
    SubmissionDraftChange,
)
from squid.core.submissions.models.finalization import (
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
)

__all__ = [
    "ApprovedSubmissionOptionCatalog",
    "PaperSponsorResolver",
    "PostgresDraftRepository",
    "PostgresFinalizationJobRepository",
    "SubmissionDraft",
    "SubmissionDraftAccess",
    "SubmissionDraftChange",
    "SubmissionFinalizationJob",
    "SubmissionFinalizationResult",
]
