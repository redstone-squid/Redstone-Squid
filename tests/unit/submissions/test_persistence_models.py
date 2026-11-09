"""Keep SQLAlchemy enum mappings and declared database check values aligned."""

import re

from sqlalchemy import CheckConstraint, Table

from squid.submissions.domain import DraftStatus, FinalizationJobStatus, SubmissionOrigin
from squid.submissions.models.entities import SubmissionDraft
from squid.submissions.models.finalization import SubmissionFinalizationJob
from tests.support.schema import model_table


def _checked_values(table: Table, constraint_name: str) -> set[str]:
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))


def test_submission_draft_enum_mappings_match_database_checks() -> None:
    table = model_table(SubmissionDraft)
    assert _checked_values(table, "submission_drafts_status_check") == {status.value for status in DraftStatus}
    assert _checked_values(table, "submission_drafts_origin_check") == {origin.value for origin in SubmissionOrigin}
    assert table.c.status.type.python_type is DraftStatus
    assert table.c.origin.type.python_type is SubmissionOrigin


def test_finalization_job_status_mapping_matches_database_check() -> None:
    table = model_table(SubmissionFinalizationJob)
    assert _checked_values(table, "submission_finalization_jobs_status_check") == {
        status.value for status in FinalizationJobStatus
    }
    assert table.c.status.type.python_type is FinalizationJobStatus
