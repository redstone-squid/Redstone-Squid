import re
from typing import cast

from sqlalchemy import CheckConstraint, Table

from squid.submissions.domain import DraftStatus, SubmissionOrigin
from squid.submissions.infrastructure.models import SubmissionDraft


def _checked_values(constraint_name: str) -> set[str]:
    table = cast(Table, SubmissionDraft.__table__)
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))


def test_submission_draft_enum_mappings_match_database_checks() -> None:
    table = cast(Table, SubmissionDraft.__table__)
    assert _checked_values("submission_drafts_status_check") == {status.value for status in DraftStatus}
    assert _checked_values("submission_drafts_origin_check") == {origin.value for origin in SubmissionOrigin}
    assert table.c.status.type.python_type is DraftStatus
    assert table.c.origin.type.python_type is SubmissionOrigin
