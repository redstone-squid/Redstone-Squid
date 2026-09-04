"""Constrain persisted record build kinds to the domain vocabulary.

Revision ID: e3f9a2b7c0d4
Revises: d2e8f1a6b9c3
Create Date: 2026-08-31 13:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e3f9a2b7c0d4"
down_revision: str | Sequence[str] | None = "d2e8f1a6b9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUILD_KIND_CHECK = "build_kind IN ('door', 'entrance', 'extender', 'utility')"
_CONSTRAINTS = (
    ("record_definitions_build_kind_check", "record_definitions"),
    ("record_competitions_build_kind_check", "record_competitions"),
    ("record_computation_runs_build_kind_check", "record_computation_runs"),
    ("record_recompute_queue_build_kind_check", "record_recompute_queue"),
)


def upgrade() -> None:
    """Reject record rows whose build kind is outside the domain enum."""
    for constraint_name, table_name in _CONSTRAINTS:
        op.create_check_constraint(constraint_name, table_name, _BUILD_KIND_CHECK)


def downgrade() -> None:
    """Remove build-kind checks while retaining the underlying text columns."""
    for constraint_name, table_name in reversed(_CONSTRAINTS):
        op.drop_constraint(constraint_name, table_name, type_="check")
