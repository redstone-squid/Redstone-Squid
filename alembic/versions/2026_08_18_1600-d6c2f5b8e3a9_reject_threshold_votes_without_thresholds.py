"""Reject threshold votes that carry no thresholds.

Revision ID: d6c2f5b8e3a9
Revises: c5b1e4a7d2f8
Create Date: 2026-08-18 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6c2f5b8e3a9"
down_revision: str | None = "c5b1e4a7d2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "vote_sessions_threshold_kind_check"

_FIXED = (
    "CASE WHEN kind = 'generic'"
    " THEN pass_threshold IS NULL AND fail_threshold IS NULL"
    " ELSE pass_threshold IS NOT NULL AND fail_threshold IS NOT NULL"
    " AND pass_threshold > 0 AND fail_threshold < 0"
    " END"
)

_ORIGINAL = (
    "CASE WHEN kind = 'generic'"
    " THEN pass_threshold IS NULL AND fail_threshold IS NULL"
    " ELSE pass_threshold > 0 AND fail_threshold < 0"
    " END"
)


def upgrade() -> None:
    # `NULL > 0` is NULL rather than false, and a check constraint only rejects on
    # false, so a build or delete-log session with no thresholds satisfied the old
    # constraint. Such a session can never reach its own closing condition.
    op.execute(
        "UPDATE vote_sessions SET pass_threshold = 3, fail_threshold = -3"
        " WHERE kind <> 'generic' AND (pass_threshold IS NULL OR fail_threshold IS NULL)"
    )
    op.drop_constraint(_CONSTRAINT, "vote_sessions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "vote_sessions", _FIXED)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "vote_sessions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "vote_sessions", _ORIGINAL)
