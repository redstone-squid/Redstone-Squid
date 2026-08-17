"""Drop a tag check that checks nothing

`tag_definitions_numeric_metadata_check` read:

    (value_type = 'numeric') = (canonical_unit_key IS NOT NULL OR numeric_step IS NOT NULL)
    OR (value_type = 'numeric' AND canonical_unit_key IS NULL AND numeric_step IS NULL)

The second disjunct is true for every numeric row whose two columns are null, and the first is
true for every numeric row where at least one is set, so a numeric row passes unconditionally.
What survives is the non-numeric case: no unit, no step. `tag_definitions_non_numeric_unit_check`
already says exactly that, and additionally covers `default_display_unit_key`, so it subsumes
this one entirely.

Rewriting it to the rule it looks like it wanted -- numeric implies a canonical unit or a step --
would break the application rather than protect it. `TagRepository.create_showcase` mints
user-authored numeric tags with `canonical_unit_key=None` and `numeric_step=None` on purpose:
a user naming a new showcase metric has no unit vocabulary to draw on, and moderation fills that
in later if it is ever needed.

No data changes; a constraint that admits every row cannot be rejecting any.

Revision ID: f7b1c4e9d6a3
Revises: e6a0b3d8c592
Create Date: 2026-08-17 12:20:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7b1c4e9d6a3"
down_revision: str | Sequence[str] | None = "e6a0b3d8c592"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "tag_definitions_numeric_metadata_check"
_EXPRESSION = (
    "(value_type = 'numeric') = (canonical_unit_key IS NOT NULL OR numeric_step IS NOT NULL) OR "
    "(value_type = 'numeric' AND canonical_unit_key IS NULL AND numeric_step IS NULL)"
)


def upgrade() -> None:
    """Apply this revision."""
    op.drop_constraint(_CONSTRAINT, "tag_definitions", type_="check")


def downgrade() -> None:
    """Revert this revision."""
    op.create_check_constraint(_CONSTRAINT, "tag_definitions", _EXPRESSION)
