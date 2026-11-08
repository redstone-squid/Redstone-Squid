"""Persistence adapters for synchronized submission drafts."""

from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.infrastructure.options import ApprovedSubmissionOptionCatalog
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from squid.submissions.infrastructure.sponsors import PaperSponsorResolver
from squid.submissions.models.entities import (
    SubmissionDraft,
    SubmissionDraftChange,
)
from squid.submissions.models.finalization import (
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
)

__all__ = [
    "ApprovedSubmissionOptionCatalog",
    "PaperSponsorResolver",
    "PostgresDraftRepository",
    "PostgresFinalizationJobRepository",
    "SubmissionDraft",
    "SubmissionDraftChange",
    "SubmissionFinalizationJob",
    "SubmissionFinalizationResult",
]
