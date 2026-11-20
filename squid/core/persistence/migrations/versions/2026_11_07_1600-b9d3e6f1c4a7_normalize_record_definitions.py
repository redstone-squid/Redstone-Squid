"""Read record identities through their stable competitions."""

import sqlalchemy as sa

from alembic import op

revision: str = "b9d3e6f1c4a7"
down_revision: str | None = "a8c2d5e0b3f6"
branch_labels: str | None = None
depends_on: str | None = None

_FIELDS = ("record_class", "build_kind", "version_scope", "version_id", "category_key")
_CHECKS = {
    "record_class": "record_class IN ('first', 'fastest', 'smallest', 'fastest_smallest', 'smallest_fastest')",
    "build_kind": "build_kind IN ('door', 'entrance', 'extender', 'utility')",
    "version_scope": "version_scope IN ('all_time', 'current')",
}


def upgrade() -> None:
    op.drop_constraint("record_definitions_identity_key", "record_definitions", type_="unique")
    op.drop_index("record_definitions_category_idx", table_name="record_definitions")
    for name in _CHECKS:
        op.drop_constraint(f"record_definitions_{name}_check", "record_definitions", type_="check")
    op.drop_constraint("record_definitions_version_id_fkey", "record_definitions", type_="foreignkey")
    for name in _FIELDS:
        op.drop_column("record_definitions", name)
    op.create_unique_constraint(
        "record_definitions_identity_key", "record_definitions", ["ruleset_id", "competition_id"]
    )
    op.create_index("record_definitions_competition_idx", "record_definitions", ["competition_id"])
    op.create_index(
        "record_competitions_category_idx", "record_competitions", ["build_kind", "record_class", "category_key"]
    )
    op.drop_index("idx_builds_record_category", table_name="builds")
    op.drop_constraint("check_record_category", "builds", type_="check")
    op.drop_column("builds", "record_category")


def downgrade() -> None:
    op.add_column("builds", sa.Column("record_category", sa.Text(), nullable=True))
    op.create_check_constraint(
        "check_record_category",
        "builds",
        "record_category = ANY (ARRAY['Smallest', 'Fastest', 'First', 'Smallest Fastest', 'Fastest Smallest', NULL])",
    )
    op.execute("CREATE INDEX idx_builds_record_category ON builds (record_category) WHERE record_category IS NOT NULL")
    op.drop_index("record_competitions_category_idx", table_name="record_competitions")
    op.drop_index("record_definitions_competition_idx", table_name="record_definitions")
    op.drop_constraint("record_definitions_identity_key", "record_definitions", type_="unique")
    for name in _FIELDS:
        op.add_column(
            "record_definitions",
            sa.Column(name, sa.SmallInteger() if name == "version_id" else sa.Text(), nullable=True),
        )
    op.execute("""
        UPDATE record_definitions d SET record_class = c.record_class, build_kind = c.build_kind,
            version_scope = c.version_scope, version_id = c.version_id, category_key = c.category_key
        FROM record_competitions c WHERE c.public_id = d.competition_id
    """)
    for name in _FIELDS:
        if name != "version_id":
            op.alter_column("record_definitions", name, nullable=False)
    op.create_foreign_key(
        "record_definitions_version_id_fkey",
        "record_definitions",
        "versions",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name, expression in _CHECKS.items():
        op.create_check_constraint(f"record_definitions_{name}_check", "record_definitions", expression)
    op.create_unique_constraint(
        "record_definitions_identity_key",
        "record_definitions",
        ["ruleset_id", *_FIELDS],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "record_definitions_category_idx", "record_definitions", ["build_kind", "record_class", "category_key"]
    )
