"""Checked persistence encoding for draft and attempt issues."""

from collections.abc import Mapping, Sequence

from squid.core.errors import DataIntegrityError
from squid.submissions.domain.finalization import SubmissionAttentionIssue, SubmissionAttentionReason


def encode_issues(issues: Sequence[SubmissionAttentionIssue]) -> list[dict[str, object]]:
    return [{"field_id": issue.field_id, "reason": issue.reason.value} for issue in issues]


def decode_issues(values: object) -> tuple[SubmissionAttentionIssue, ...]:
    msg = "persisted submission attention issues are invalid"
    if not isinstance(values, list):
        raise DataIntegrityError(msg)
    issues: list[SubmissionAttentionIssue] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise DataIntegrityError(msg)
        try:
            field_id = value["field_id"]
            reason = value["reason"]
        except KeyError as error:
            raise DataIntegrityError(msg) from error
        if not isinstance(field_id, str) or not isinstance(reason, str):
            raise DataIntegrityError(msg)
        try:
            issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason(reason)))
        except ValueError as error:
            raise DataIntegrityError(msg) from error
    return tuple(issues)
