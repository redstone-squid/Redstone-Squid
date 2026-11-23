"""Who may edit how much of a build."""

import pytest

from squid.core.builds.domain import BuildStatus, EditAccess
from squid.core.builds.models import OtherBuild


@pytest.mark.parametrize(
    ("account_id", "status", "may_edit_any", "expected"),
    [
        (1, BuildStatus.PENDING, False, EditAccess.FULL),
        (1, BuildStatus.APPROVED, False, EditAccess.FULL),
        (1, BuildStatus.REJECTED, False, EditAccess.FULL),
        (2, BuildStatus.PENDING, False, EditAccess.NONE),
        (None, BuildStatus.PENDING, False, EditAccess.NONE),
        (2, BuildStatus.APPROVED, True, EditAccess.FULL),
    ],
)
def test_edit_access(account_id: int | None, status: BuildStatus, may_edit_any: bool, expected: EditAccess) -> None:
    build = OtherBuild(submitter_account_id=1, submission_status=status)

    assert build.edit_access(account_id, may_edit_any=may_edit_any) is expected


def test_a_credited_creator_can_open_the_editor() -> None:
    build = OtherBuild(submitter_account_id=1, submission_status=BuildStatus.APPROVED)
    build.credited_creator_account_ids = frozenset({2})
    assert build.edit_access(2, may_edit_any=False) is EditAccess.FULL
