"""Who may edit how much of a build."""

import pytest

from squid.builds.domain import EditAccess, Status
from squid.builds.models import OtherBuild


@pytest.mark.parametrize(
    ("account_id", "status", "may_edit_any", "expected"),
    [
        (1, Status.PENDING, False, EditAccess.FULL),
        (1, Status.CONFIRMED, False, EditAccess.SHOWCASE_TAGS),
        (1, Status.DENIED, False, EditAccess.SHOWCASE_TAGS),
        (2, Status.PENDING, False, EditAccess.NONE),
        (None, Status.PENDING, False, EditAccess.NONE),
        (2, Status.CONFIRMED, True, EditAccess.FULL),
    ],
)
def test_edit_access(account_id: int | None, status: Status, may_edit_any: bool, expected: EditAccess) -> None:
    build = OtherBuild(submitter_account_id=1, submission_status=status)

    assert build.edit_access(account_id, may_edit_any=may_edit_any) is expected
